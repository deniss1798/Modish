"""Real HTTP admission for the default-off optional paid image provider."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import os
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.api import onboarding
from app.api.deps import create_access_token, get_db
from app.config import style_visual_enabled
from app.db import Base
from app.models import PhotoAnalysisReservation, StyleProfile, User, UserLimits
from app.services import photo_analysis_jobs
from app.services.recommendation_capabilities import recommendation_features


ANALYSIS = {'color_palette': ['black'], 'style_directions': ['casual']}


class StyleVisualConfigurationTests(unittest.TestCase):
    def test_only_explicit_opt_in_enables_the_paid_provider(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(style_visual_enabled())
        for value in ('true', '1', ' TRUE '):
            with self.subTest(value=value), patch.dict(os.environ,
                    {'STYLE_VISUAL_ENABLED': value}):
                self.assertTrue(style_visual_enabled())
        for value in ('false', '0', '', 'yes', 'on', 'tru', 'unexpected-setting'):
            with self.subTest(value=value), patch.dict(os.environ,
                    {'STYLE_VISUAL_ENABLED': value}):
                self.assertFalse(style_visual_enabled())

    def test_capability_uses_the_same_live_policy_for_cold_and_completed_responses(self):
        for enabled in (False, True):
            for activation in (False, True):
                with self.subTest(enabled=enabled, activation=activation), patch.dict(
                    os.environ, {'STYLE_VISUAL_ENABLED': 'true' if enabled else 'false'}
                ):
                    features = recommendation_features(activation=activation)
                    self.assertEqual('style_visual_v1' in features, enabled)
                    features.append('caller-mutation')
                    self.assertNotIn('caller-mutation',
                                     recommendation_features(activation=activation))
                    self.assertIn('catalogue_discovery_v1', features)
                    self.assertIn('shopping_intent_v1', features)


class StyleVisualHTTPTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False},
                                    poolclass=StaticPool)
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, autoflush=False)
        jobs = patch.object(photo_analysis_jobs, 'SessionLocal', self.sessions)
        jobs.start()
        self.addCleanup(jobs.stop)
        policy = patch.dict(os.environ, {'CLOSED_ALPHA_ENABLED': 'false',
                                        'STYLE_VISUAL_ENABLED': 'false'})
        policy.start()
        self.addCleanup(policy.stop)
        with self.sessions() as db:
            now = datetime.now(timezone.utc)
            db.add(User(id='public-visual-owner', email='visual-owner@example.com',
                password_hash='unused', plan='free', subscription_status='inactive',
                trial_started_at=now - timedelta(days=30),
                trial_ends_at=now - timedelta(days=16)))
            db.flush()
            db.add(StyleProfile(id='public-visual-profile', user_id='public-visual-owner',
                                profile_json={'analysis': deepcopy(ANALYSIS)}))
            db.commit()
        self.headers = {'Authorization': 'Bearer ' + create_access_token('public-visual-owner')}
        # Exercise the real endpoint and onboarding routes with actual auth/DB.
        # A separate ASGI app avoids sharing unrelated middleware quotas across
        # independent tests; the optional provider is always an explicit double.
        app = FastAPI()
        app.add_api_route('/visual-analysis', main.visual_analysis, methods=['POST'])
        app.include_router(onboarding.router)

        def database():
            with self.sessions() as db:
                yield db

        app.dependency_overrides[get_db] = database
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def post(self, headers=None):
        return self.client.post('/visual-analysis',
                                headers=self.headers if headers is None else headers)

    def test_disabled_rejects_even_a_preexisting_analysis_before_snapshot_or_provider(self):
        with patch.object(main, 'detached_visual_analysis', new_callable=AsyncMock) as snapshot, \
                patch.object(main, 'generate_style_visual', new_callable=AsyncMock) as provider:
            for headers in (self.headers, {}, {'Authorization': 'Bearer invalid'}):
                response = self.post(headers)
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json(), {'detail': main.STYLE_VISUAL_DISABLED_MESSAGE})
                self.assertEqual(response.headers['cache-control'], 'no-store')
            snapshot.assert_not_awaited()
            provider.assert_not_awaited()
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(StyleProfile)).profile_json['analysis'], ANALYSIS)
            self.assertEqual(db.scalars(select(PhotoAnalysisReservation)).all(), [])
            self.assertEqual(db.scalars(select(UserLimits)).all(), [])

    def test_malformed_flag_is_not_an_opt_in_and_does_not_leak_its_value(self):
        with patch.dict(os.environ, {'STYLE_VISUAL_ENABLED': 'unexpected-setting'}), \
                patch.object(main, 'generate_style_visual', new_callable=AsyncMock) as provider:
            response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('unexpected-setting', response.text)
        provider.assert_not_awaited()

    def test_opt_in_preserves_authenticated_success_without_claiming_a_new_quota(self):
        with patch.dict(os.environ, {'STYLE_VISUAL_ENABLED': 'true'}), patch.object(
            main, 'generate_style_visual', new_callable=AsyncMock,
            return_value='https://example.com/generated.jpg',
        ) as provider:
            response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'image_url': 'https://example.com/generated.jpg',
                                           'type': 'style_visual'})
        provider.assert_awaited_once_with(ANALYSIS)
        # Enabling is an operator opt-in to the old unmetered feature. It must
        # not silently charge or modify the independent photo reservation ledger.
        with self.sessions() as db:
            self.assertEqual(db.scalars(select(PhotoAnalysisReservation)).all(), [])
            self.assertEqual(db.scalars(select(UserLimits)).all(), [])

    def test_opt_in_still_requires_a_real_bearer_token(self):
        with patch.dict(os.environ, {'STYLE_VISUAL_ENABLED': '1'}), \
                patch.object(main, 'generate_style_visual', new_callable=AsyncMock) as provider:
            for headers in ({}, {'Authorization': 'Bearer invalid'}):
                self.assertEqual(self.post(headers).status_code, 401)
            provider.assert_not_awaited()

    def test_opt_in_does_not_invent_an_analysis_or_echo_provider_errors(self):
        with self.sessions() as db:
            db.scalar(select(StyleProfile)).profile_json = {}
            db.commit()
        with patch.dict(os.environ, {'STYLE_VISUAL_ENABLED': 'true'}), \
                patch.object(main, 'generate_style_visual', new_callable=AsyncMock) as provider:
            self.assertEqual(self.post().status_code, 400)
            provider.assert_not_awaited()
        with self.sessions() as db:
            db.scalar(select(StyleProfile)).profile_json = {'analysis': deepcopy(ANALYSIS)}
            db.commit()
        with patch.dict(os.environ, {'STYLE_VISUAL_ENABLED': 'true'}), patch.object(
            main, 'generate_style_visual', new_callable=AsyncMock,
            side_effect=RuntimeError('private-provider-diagnostic'),
        ) as provider:
            response = self.post()
        self.assertEqual(response.status_code, 502)
        self.assertNotIn('private-provider-diagnostic', response.text)
        provider.assert_awaited_once()

    def test_actual_status_and_preferences_advertise_visual_only_when_opted_in(self):
        for value, available in (('false', False), ('true', True), ('false', False)):
            with patch.dict(os.environ, {'STYLE_VISUAL_ENABLED': value}):
                response = self.client.get('/onboarding/status', headers=self.headers)
                self.assertEqual(response.status_code, 200, response.text)
                features = response.json()['recommendation_features']
                self.assertEqual('style_visual_v1' in features, available)
                preferences = self.client.post('/onboarding/preferences',
                    headers=self.headers, json={
                    'gender_target': 'womenswear', 'age_group': '25-34',
                    'clothing_size': 'M', 'clothing_size_system': 'LETTER',
                    'shoe_size': '38', 'shoe_size_system': 'EU', 'height': 170,
                    'styles': ['casual', 'minimalism'], 'price_segment': 'mass',
                })
                self.assertEqual(preferences.status_code, 200, preferences.text)
                features = preferences.json()['recommendation_features']
                self.assertEqual('style_visual_v1' in features, available)


if __name__ == '__main__':
    unittest.main()
