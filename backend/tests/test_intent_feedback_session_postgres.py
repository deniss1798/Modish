"""Atomic permanent Intent feedback over actual HTTP and disposable PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import os
import threading
import unittest
from unittest.mock import patch

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from app.api import shopping_intents as api
from app.api.deps import get_db
from app.models import (ActivationExposure, MetricEvent, Product, RecommendationEventV2,
    ShoppingIntent, TasteProfile, User, UserProductState, UserTasteFeature)
from app.services import intent_mutation_session as mutations, shopping_intent_service as service
from app.services.behavior_profile import load_behavior_profile
from app.services.feedback_preview import current_feedback_preview
from app.services.mie_scoring import load_user_taste_feature_map
from app.services.personalization_scoring import load_ranking_seen_counts
from app.services.profile_repository import load_preferences
from isolated_postgres import create_isolated_postgres
import test_intent_outfit_create_session as fixtures


def normalized(value):
    if isinstance(value, datetime):
        return service._utc(value).isoformat()
    if isinstance(value, dict):
        return {key: normalized(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalized(item) for item in value]
    return value


@unittest.skipUnless(os.getenv('MODISH_ISOLATED_PG_RUNTIME'), 'Opt-in disposable PostgreSQL fixture')
class IntentFeedbackSessionPostgresTests(unittest.TestCase):
    def setUp(self):
        isolated = create_isolated_postgres(self, namespace='intent_feedback', pool_size=4)
        self.isolated, self.engine = isolated, isolated.engine
        with Session(self.engine, autoflush=False) as db:
            self.user_id = fixtures.seed_outfit_catalogue(db, user_id='feedback-pg-owner')
        app, self.auth_reads = FastAPI(), []
        app.include_router(api.router)

        def database(request: Request):
            with Session(self.engine, autoflush=False) as db:
                if request.url.path.endswith('/feedback'):
                    self.auth_reads.append(db)
                yield db

        app.dependency_overrides[get_db] = database
        auth = patch.object(api, 'user_from_token', side_effect=lambda credentials, db: db.get(User, self.user_id))
        auth.start()
        self.addCleanup(auth.stop)
        self.client = TestClient(app, raise_server_exceptions=False)
        self.addCleanup(self.client.close)

    def create(self, key='feedback-stable-request-key'):
        response = self.client.post('/shopping-intents', json={'text': 'Рубашки до 6000', 'request_id': key})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()['products']), 3)
        return response.json()

    def feedback(self, current, product_id=None, reason='dont_like_color'):
        return self.client.post(f"/shopping-intents/{current['id']}/feedback", json={
            'expected_version': current['version'], 'product_id': product_id or current['products'][0]['product']['id'],
            'reason': reason})

    def seed_exposures(self, *product_ids):
        with Session(self.engine) as db:
            for position, product_id in enumerate(product_ids, 1):
                db.add(ActivationExposure(id=f'feedback-existing-{position}', user_id=self.user_id,
                    product_id=product_id, position=position, reaction='like', saved=1, opened=1, clicked=1))
            db.commit()

    def snapshot(self, intent_ids):
        with Session(self.engine) as db:
            result = {'revision': db.get(User, self.user_id).recommendation_revision}
            models = (ShoppingIntent, TasteProfile, UserTasteFeature, RecommendationEventV2,
                UserProductState, ActivationExposure, MetricEvent)
            for model in models:
                query = select(model).where(model.user_id == self.user_id)
                if model is ShoppingIntent:
                    query = query.where(model.id.in_(intent_ids))
                rows = [normalized({column.name: getattr(row, column.name) for column in model.__table__.columns})
                    for row in db.scalars(query)]
                result[model.__tablename__] = sorted(rows, key=lambda row: json.dumps(row, sort_keys=True))
            return result

    def counts(self, current):
        with Session(self.engine) as db:
            return fixtures.metric_counts(db, current['id'])

    def assert_once(self, current):
        counts = self.counts(current)
        self.assertEqual(counts['intent_feedback'], 1)
        self.assertEqual(counts['intent_updated'], 1)
        self.assertEqual(counts['intent_results_generated'], 2)
        with Session(self.engine) as db:
            self.assertEqual(db.get(ShoppingIntent, current['id']).version, 2)
            events = [row for row in db.scalars(select(RecommendationEventV2).where(
                RecommendationEventV2.user_id == self.user_id)) if row.meta_json.get('intent_id') == current['id']]
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].meta_json['intent_version'], 1)

    @contextmanager
    def ranking_barrier(self, *, intent_id=None):
        ready, release, calls = threading.Event(), threading.Event(), []
        original = mutations.item_results

        def rank(db, user, spec, **options):
            self.assertTrue(db.info['recommendation_read_only'])
            self.assertNotIn('_recommendation_owner_mutations', db.info)
            self.assertEqual(db.scalar(text('SHOW transaction_read_only')), 'on')
            self.assertEqual(db.scalar(text('SHOW transaction_isolation')), 'repeatable read')
            preview = current_feedback_preview(db, user.id)
            self.assertIsNotNone(preview)
            current_id = preview.events[0].metadata()['intent_id']
            taste = load_preferences(db, user)[2]
            rows = original(db, user, spec, **options)
            calls.append((current_id, deepcopy(taste.color_weights), [row['product']['id'] for row in rows]))
            if (intent_id is None or current_id == intent_id) and not ready.is_set():
                self.assertTrue(rows)
                ready.set()
                if not release.wait(6):
                    raise AssertionError('Missing deterministic feedback ranking release')
            return rows

        with patch.object(mutations, 'item_results', side_effect=rank):
            try:
                yield ready, release, calls
            finally:
                release.set()

    def test_another_intent_feedback_commits_during_readonly_rank_then_first_retries(self):
        first, other = self.create(), self.create('feedback-second-request-key')
        first_target = first['products'][0]['product']['id']
        other_target = next(row['product']['id'] for row in other['products'] if row['product']['id'] != first_target)
        self.seed_exposures(first_target, other_target)
        baseline = self.snapshot([first['id'], other['id']])
        with self.ranking_barrier(intent_id=first['id']) as (ready, release, calls):
            with ThreadPoolExecutor(max_workers=2) as executor:
                pending = executor.submit(self.feedback, first, first_target)
                try:
                    self.assertTrue(ready.wait(5))
                    self.assertFalse(self.auth_reads[0].in_transaction())
                    self.assertEqual(len(self.auth_reads[0].identity_map), 0)
                    self.assertEqual(self.snapshot([first['id'], other['id']]), baseline)
                    completed = executor.submit(self.feedback, other, other_target).result(timeout=3)
                    self.assertEqual(completed.status_code, 200, completed.text)
                    self.assertEqual(self.counts(first)['intent_feedback'], 0)
                    with Session(self.engine) as db:
                        self.assertEqual(db.get(ShoppingIntent, first['id']).version, 1)
                finally:
                    release.set()
                response = pending.result(timeout=6)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(calls), 3)
        self.assertEqual([weights['black'] for intent_id, weights, _ in calls if intent_id == first['id']], [-3, -6])
        self.assertNotIn(first_target, {row['product']['id'] for row in response.json()['products']})
        self.assert_once(first)
        self.assert_once(other)
        with Session(self.engine) as db:
            self.assertEqual(db.get(User, self.user_id).recommendation_revision, baseline['revision'] + 2)

    def test_same_intent_two_feedbacks_have_one_cas_winner_and_no_loser_side_effects(self):
        current = self.create()
        stale_target, winner_target = [row['product']['id'] for row in current['products'][:2]]
        self.seed_exposures(stale_target, winner_target)
        revision = self.snapshot([current['id']])['revision']
        with self.ranking_barrier() as (ready, release, calls):
            with ThreadPoolExecutor(max_workers=2) as executor:
                pending = executor.submit(self.feedback, current, stale_target, 'dont_like_color')
                try:
                    self.assertTrue(ready.wait(5))
                    winner = executor.submit(self.feedback, current, winner_target, 'dont_like_brand').result(timeout=3)
                    self.assertEqual(winner.status_code, 200, winner.text)
                    after_winner = self.snapshot([current['id']])
                finally:
                    release.set()
                loser = pending.result(timeout=6)
        self.assertEqual(loser.status_code, 409, loser.text)
        self.assertEqual(len(calls), 2, 'CAS conflict must not rerank the newer Intent version')
        self.assertEqual(self.snapshot([current['id']]), after_winner)
        self.assertEqual(after_winner['revision'], revision + 1)
        self.assert_once(current)
        with Session(self.engine) as db:
            self.assertIsNone(db.scalar(select(UserProductState).where(UserProductState.product_id == stale_target)))
            self.assertEqual(db.get(ActivationExposure, 'feedback-existing-1').reaction, 'like')
            self.assertEqual(db.get(ActivationExposure, 'feedback-existing-2').reaction, 'dislike')
            taste = db.scalar(select(TasteProfile).where(TasteProfile.user_id == self.user_id))
            self.assertEqual(taste.color_weights, {})
            self.assertEqual(taste.brand_weights['synthetic outfit brand'], -3)

    def test_two_catalogue_drifts_return_409_without_feedback_or_personal_leaks(self):
        current = self.create()
        target = current['products'][0]['product']['id']
        self.seed_exposures(target)
        baseline = self.snapshot([current['id']])
        ready = [threading.Event(), threading.Event()]
        releases = [threading.Event(), threading.Event()]
        calls, original = [], mutations.item_results

        def rank(db, *args, **options):
            rows = original(db, *args, **options)
            index = len(calls)
            calls.append(rows)
            self.assertLess(index, 2)
            ready[index].set()
            if not releases[index].wait(6):
                raise AssertionError('Missing deterministic catalogue drift release')
            return rows

        with patch.object(mutations, 'item_results', side_effect=rank):
            with ThreadPoolExecutor(max_workers=2) as executor:
                pending = executor.submit(self.feedback, current, target)
                try:
                    for index in range(2):
                        self.assertTrue(ready[index].wait(5))
                        with Session(self.engine) as db:
                            db.get(Product, target).price += Decimal('0.01')
                            db.commit()
                        self.assertEqual(self.snapshot([current['id']]), baseline)
                        releases[index].set()
                finally:
                    for release in releases:
                        release.set()
                response = pending.result(timeout=6)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.snapshot([current['id']]), baseline)

    def test_failure_after_accepting_event_rolls_back_state_taste_exposure_and_successor(self):
        current = self.create()
        target = current['products'][0]['product']['id']
        self.seed_exposures(target)
        baseline = self.snapshot([current['id']])
        reached = []

        def fail_after_event(db, *_args):
            events = list(db.scalars(select(RecommendationEventV2).where(RecommendationEventV2.user_id == self.user_id)))
            state = db.scalar(select(UserProductState).where(UserProductState.product_id == target))
            taste = db.scalar(select(TasteProfile).where(TasteProfile.user_id == self.user_id))
            reached.append((len(events), events[0].meta_json['intent_version'] if events else None,
                bool(state and state.hidden_until), dict(taste.color_weights),
                db.get(ActivationExposure, 'feedback-existing-1').reaction))
            raise RuntimeError('SYNTHETIC_PG_PUBLICATION_FAILURE')

        with patch.object(service, 'remember_results', side_effect=fail_after_event) as remembering:
            response = self.feedback(current)
        self.assertEqual(response.status_code, 500)
        remembering.assert_called_once()
        self.assertEqual(reached, [(1, 1, True, {'black': -3}, 'dislike')])
        self.assertEqual(self.snapshot([current['id']]), baseline)

    def test_non_utc_pg_session_preserves_preview_behavior_and_product_day_parity(self):
        # The same fenced server/schema, with a distinct pool and decoder timezone.
        engine = create_engine(self.engine.url, pool_size=4, max_overflow=0, connect_args={
            'connect_timeout': 3,
            'options': f'-c search_path={self.isolated.schema} -c timezone=Europe/Moscow -c statement_timeout=5000'})
        self.addCleanup(engine.dispose)
        with self.isolated.admin.connect() as db:
            directory = db.scalar(text('SHOW data_directory'))
        with engine.connect() as db:
            self.assertEqual(db.scalar(text('SHOW data_directory')), directory)
            self.assertEqual(db.scalar(text('SHOW port')), '55439')
            self.assertEqual(db.scalar(text('SHOW TimeZone')), 'Europe/Moscow')
        self.engine = engine
        current = self.create()
        target = current['products'][0]['product']['id']
        self.seed_exposures(target)
        now = datetime.now(timezone.utc)
        clock = now.replace(hour=22, minute=30, second=0, microsecond=0)
        if clock < now:
            clock += timedelta(days=1)
        with Session(engine) as db:
            product = db.get(Product, target)
            db.add(RecommendationEventV2(id='earlier-same-database-day', user_id=self.user_id, product_id=target,
                event_type='view', event_weight=.1, created_at=clock - timedelta(minutes=10),
                meta_json={'observed_brand': product.brand, 'observed_category': product.category,
                    'observed_price': float(product.price)}))
            db.commit()
        projected, original = [], mutations.item_results

        def rank(db, user, spec, **options):
            self.assertEqual(db.scalar(text('SHOW TimeZone')), 'Europe/Moscow')
            self.assertEqual(db.scalar(text('SHOW transaction_read_only')), 'on')
            features = load_user_taste_feature_map(db, user_id=user.id)
            projected.append((deepcopy(features), load_behavior_profile(db, user_id=user.id),
                load_ranking_seen_counts(db, user_id=user.id)))
            return original(db, user, spec, **options)

        with patch.object(mutations, '_now', return_value=clock), patch.object(mutations, 'item_results', side_effect=rank):
            response = self.feedback(current, target, 'dont_like_brand')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(projected), 1)
        with Session(engine) as db:
            actual = (load_user_taste_feature_map(db, user_id=self.user_id, as_of=clock),
                load_behavior_profile(db, user_id=self.user_id, as_of=clock),
                load_ranking_seen_counts(db, user_id=self.user_id, as_of=clock))
            self.assertEqual(actual, projected[0])
            self.assertEqual(actual[2][target], 1)
            brand = actual[1]['brands']['synthetic outfit brand']
            self.assertEqual((brand['exposure_count'], brand['negative']), (1, 1))
            accepted = db.scalar(select(RecommendationEventV2).where(RecommendationEventV2.event_type == 'dislike'))
            self.assertEqual(accepted.created_at.utcoffset(), timedelta(hours=3))
            self.assertEqual(accepted.created_at.date(), (clock + timedelta(days=1)).date())
            self.assertEqual(db.scalar(select(func.date(RecommendationEventV2.created_at)).where(
                RecommendationEventV2.id == accepted.id)), accepted.created_at.date())
        self.assert_once(current)


if __name__ == '__main__':
    unittest.main()
