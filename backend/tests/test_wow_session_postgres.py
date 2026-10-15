"""Actual Wow-completion HTTP races on disposable PostgreSQL only."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import os
import threading
import unittest
from unittest.mock import patch

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api import onboarding, recommendations as feedback_api
from app.api.deps import get_db
from app.models import ActivationSession, MetricEvent, Outfit, RecommendationEventV2, StyleProfile, TasteProfile, User
from app.services import wow_session as wow
from isolated_postgres import create_isolated_postgres
import test_wow_session as fixtures


@unittest.skipUnless(os.getenv('MODISH_ISOLATED_PG_RUNTIME'), 'Opt-in disposable PostgreSQL fixture')
class WowSessionPostgresTests(unittest.TestCase):
    def setUp(self):
        isolated = create_isolated_postgres(self, namespace='wow')
        self.engine = isolated.engine
        with Session(self.engine, autoflush=False) as db:
            self.user_id = fixtures.seed_wow_catalogue(db)
        app, self.auth_reads = FastAPI(), []
        app.include_router(onboarding.router)
        app.include_router(feedback_api.router)

        def database(request: Request):
            with Session(self.engine, autoflush=False) as db:
                if request.url.path == '/onboarding/complete':
                    self.auth_reads.append(db)
                yield db

        def authenticated_owner(credentials, db):
            return db.get(User, self.user_id)

        app.dependency_overrides[get_db] = database
        for api in (onboarding, feedback_api):
            auth = patch.object(api, 'user_from_token', side_effect=authenticated_owner)
            auth.start()
            self.addCleanup(auth.stop)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def complete(self):
        return self.client.post('/onboarding/complete')

    def revision(self):
        with Session(self.engine) as db:
            return db.get(User, self.user_id).recommendation_revision

    def state(self):
        with Session(self.engine) as db:
            return {model.__name__: [fixtures.row_values(row) for row in db.scalars(
                select(model).order_by(*model.__table__.primary_key.columns))]
                for model in (ActivationSession, StyleProfile, TasteProfile, Outfit, MetricEvent, RecommendationEventV2)}

    @contextmanager
    def ranking_barrier(self):
        ready, release, calls = threading.Event(), threading.Event(), []
        original = wow.engine.prepare_outfit_batch

        def rank(db, *args, **kwargs):
            self.assertTrue(db.info['recommendation_read_only'])
            self.assertNotIn('_recommendation_owner_mutations', db.info)
            self.assertEqual(db.scalar(text('SHOW transaction_read_only')), 'on')
            self.assertEqual(db.scalar(text('SHOW transaction_isolation')), 'repeatable read')
            batch = original(db, *args, **kwargs)
            scalar_items = tuple({slot: item.product.id for slot, item in choice.items.items()} for choice in batch.selected)
            calls.append(scalar_items)
            if len(calls) == 1:
                self.assertTrue(scalar_items)
                ready.set()
                if not release.wait(4):
                    raise AssertionError('Missing deterministic Wow ranking release')
            return batch

        with patch.object(wow.engine, 'prepare_outfit_batch', side_effect=rank):
            try:
                yield ready, release, calls
            finally:
                release.set()

    def test_feedback_http_commits_during_read_only_rank_and_no_stale_completion_publishes(self):
        before_revision = self.revision()
        with self.ranking_barrier() as (ready, release, calls), patch.object(wow, '_prepare', wraps=wow._prepare) as preparing:
            with ThreadPoolExecutor(max_workers=2) as executor:
                delivery = executor.submit(self.complete)
                try:
                    self.assertTrue(ready.wait(4))
                    self.assertFalse(self.auth_reads[0].in_transaction())
                    self.assertEqual(len(self.auth_reads[0].identity_map), 0)
                    product_id = calls[0][0]['shoes']
                    feedback = executor.submit(self.client.post, '/recommendations/events', json={
                        'product_id': product_id, 'event_type': 'dislike', 'meta': {'surface': 'feed'},
                    }).result(timeout=2)
                    self.assertEqual(feedback.status_code, 200, feedback.text)
                    during = self.state()
                    self.assertIsNone(during['ActivationSession'][0]['completed_at'])
                    self.assertEqual(during['Outfit'], [])
                    self.assertFalse([row for row in during['MetricEvent'] if row['name'].startswith('outfit_rotation:')])
                    self.assertEqual(len(during['RecommendationEventV2']), 16)
                    self.assertEqual(self.revision(), before_revision + 1)
                finally:
                    release.set()
                response = delivery.result(timeout=5)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(preparing.call_count, 2)
        fixtures.assert_honest_wow(self, response.json(), forbidden={product_id})
        after = self.state()
        self.assertEqual(after['TasteProfile'], during['TasteProfile'])
        self.assertEqual(after['RecommendationEventV2'], during['RecommendationEventV2'])
        self.assertEqual(self.revision(), before_revision + 2)
        self.assertEqual(len(after['Outfit']), len(response.json()['outfits']))
        self.assertEqual(len(after['MetricEvent']), len(during['MetricEvent']) + len(response.json()['outfits']))
        self.assertEqual({row['id'] for row in after['Outfit']}, {row['id'] for row in response.json()['outfits']})

    def test_concurrent_complete_replays_one_committed_snapshot_without_duplicate_rows_or_taste(self):
        before_revision, before = self.revision(), self.state()
        with self.ranking_barrier() as (ready, release, calls), patch.object(wow, '_prepare', wraps=wow._prepare) as preparing:
            with ThreadPoolExecutor(max_workers=2) as executor:
                earlier = executor.submit(self.complete)
                try:
                    self.assertTrue(ready.wait(4))
                    winner = executor.submit(self.complete).result(timeout=3)
                    self.assertEqual(winner.status_code, 200, winner.text)
                    fixtures.assert_honest_wow(self, winner.json())
                    self.assertEqual(self.revision(), before_revision + 1)
                finally:
                    release.set()
                replay = earlier.result(timeout=5)
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(preparing.call_count, 3, 'The stale completion retries as an issued-snapshot replay')
        self.assertEqual(len(calls), 6, 'The replay phase itself must not compose any outfit')
        self.assertEqual(replay.json(), winner.json())
        after = self.state()
        self.assertEqual(self.revision(), before_revision + 1)
        self.assertEqual(after['TasteProfile'], before['TasteProfile'])
        self.assertEqual(after['RecommendationEventV2'], before['RecommendationEventV2'])
        self.assertEqual(len(after['Outfit']), 3)
        self.assertEqual(len(after['MetricEvent']), len(before['MetricEvent']) + 3)
        self.assertEqual([row['id'] for row in after['ActivationSession'][0]['wow_json']],
            [row['id'] for row in winner.json()['outfits']])
        with patch.object(wow.engine, 'prepare_outfit_batch', side_effect=AssertionError('Completed HTTP retry must not rank')):
            retry = self.complete()
        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertEqual(retry.json(), winner.json())
        self.assertEqual(self.state(), after)
