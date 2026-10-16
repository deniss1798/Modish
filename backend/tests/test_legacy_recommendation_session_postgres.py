"""Legacy JSON projection and generation quota commit with real outfits."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
import os
import threading
import unittest
from unittest.mock import patch

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app import business
from app.api import recommendations as api
from app.api.deps import get_db
from app.models import MetricEvent, Outfit, Recommendation, TasteProfile, User, UserLimits
from app.services import legacy_outfit_session as legacy, outfit_session as outfits
from app.services.recommendation_revision import RecommendationSnapshotChanged
from isolated_postgres import create_isolated_postgres
import test_legacy_recommendation_session as fixtures


@unittest.skipUnless(os.getenv('MODISH_ISOLATED_PG_RUNTIME'), 'Opt-in disposable PostgreSQL fixture')
class LegacyRecommendationSessionPostgresTests(unittest.TestCase):
    def setUp(self):
        isolated = create_isolated_postgres(self, namespace='legacy_outfit')
        self.engine = isolated.engine
        with Session(self.engine, autoflush=False) as db:
            self.user_id = fixtures.seed_legacy_catalogue(db)
        app, self.auth_reads = FastAPI(), []
        app.include_router(api.router)

        def database(request: Request):
            with Session(self.engine, autoflush=False) as db:
                if request.url.path == '/recommendations/generate':
                    self.auth_reads.append(db)
                yield db

        app.dependency_overrides[get_db] = database
        auth = patch.object(api, 'user_from_token', side_effect=lambda credentials, db: db.get(User, self.user_id))
        auth.start()
        self.addCleanup(auth.stop)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def generate(self):
        return self.client.post('/recommendations/generate', json={'type': 'outfit', 'count': 1, 'scenario': 'daily'})

    def state(self):
        with Session(self.engine) as db:
            taste = db.scalar(select(TasteProfile).where(TasteProfile.user_id == self.user_id))
            return (db.get(User, self.user_id).recommendation_revision,
                db.scalar(select(UserLimits.recommendation_batches_used)),
                tuple(db.scalar(select(func.count()).select_from(model)) for model in (Outfit, Recommendation, MetricEvent)),
                deepcopy({column.name: getattr(taste, column.name) for column in taste.__table__.columns}))

    @contextmanager
    def ranking_barrier(self):
        original, ready, release, calls = outfits.engine.prepare_outfit_batch, threading.Event(), threading.Event(), []

        def rank(db, user, **kwargs):
            self.assertTrue(db.info['recommendation_read_only'])
            self.assertNotIn('_recommendation_owner_mutations', db.info)
            self.assertEqual(db.scalar(text('SHOW transaction_read_only')), 'on')
            self.assertEqual(db.scalar(text('SHOW transaction_isolation')), 'repeatable read')
            batch = original(db, user, **kwargs)
            calls.append(tuple(item.product.id for choice in batch.selected for item in choice.items.values()))
            if len(calls) == 1:
                self.assertTrue(calls[0])
                ready.set()
                if not release.wait(4):
                    raise AssertionError('Missing deterministic legacy outfit ranking release')
            return batch

        with patch.object(outfits.engine, 'prepare_outfit_batch', side_effect=rank):
            try:
                yield ready, release, calls
            finally:
                release.set()

    def test_feedback_http_can_commit_during_rank_before_atomic_legacy_projection_and_charge(self):
        before = self.state()
        with self.ranking_barrier() as (ready, release, calls):
            with ThreadPoolExecutor(max_workers=2) as executor:
                delivery = executor.submit(self.generate)
                try:
                    self.assertTrue(ready.wait(4))
                    self.assertFalse(self.auth_reads[0].in_transaction())
                    self.assertEqual(len(self.auth_reads[0].identity_map), 0)
                    feedback = executor.submit(self.client.post, '/recommendations/events', json={
                        'product_id': calls[0][0], 'event_type': 'like', 'meta': {'surface': 'feed'},
                    }).result(timeout=2)
                    self.assertEqual(feedback.status_code, 200, feedback.text)
                    after_feedback = self.state()
                    self.assertEqual(after_feedback[1], 0)
                    self.assertEqual(after_feedback[2][:2], (0, 0))
                finally:
                    release.set()
                response = delivery.result(timeout=5)
        self.assertEqual(response.status_code, 200, response.text)
        fixtures.assert_legacy_batch(self, response.json())
        self.assertEqual(len(calls), 2)
        after = self.state()
        self.assertEqual(after[0], before[0] + 2)
        self.assertEqual(after[1], 1)
        self.assertEqual(after[2][:2], (1, 1))
        self.assertEqual(after[3], after_feedback[3])

    def test_two_preflight_admissions_with_one_quota_slot_publish_only_one_batch(self):
        with Session(self.engine) as db:
            db.scalar(select(UserLimits)).recommendation_batches_used = business.PLUS_MONTHLY_GENERATION_BATCHES - 1
            db.commit()
        before = self.state()
        with self.ranking_barrier() as (ready, release, calls):
            with ThreadPoolExecutor(max_workers=2) as executor:
                earlier = executor.submit(self.generate)
                try:
                    self.assertTrue(ready.wait(4))
                    winner = executor.submit(self.generate).result(timeout=2)
                    self.assertEqual(winner.status_code, 200, winner.text)
                    fixtures.assert_legacy_batch(self, winner.json())
                finally:
                    release.set()
                loser = earlier.result(timeout=5)
        self.assertEqual(loser.status_code, 403, loser.text)
        self.assertEqual(len(calls), 3, 'The stale admitted request retries once before authoritative quota denial')
        after = self.state()
        self.assertEqual(after[0], before[0] + 1)
        self.assertEqual(after[1], business.PLUS_MONTHLY_GENERATION_BATCHES)
        self.assertEqual(after[2], (1, 1, 1))
        self.assertEqual(after[3], before[3])

    def test_failure_after_projection_and_quota_flush_leaves_no_partial_legacy_publication(self):
        before, original, calls = self.state(), legacy._project_and_charge, []

        def project(db, user, rows, response, rules):
            payload = original(db, user, rows, response, rules)
            fixtures.assert_legacy_batch(self, payload)
            self.assertEqual(db.scalar(select(UserLimits.recommendation_batches_used)), 1)
            self.assertEqual(db.scalar(select(func.count()).select_from(Recommendation)), 1)
            calls.append(payload)
            raise RecommendationSnapshotChanged()

        with patch.object(legacy, '_project_and_charge', side_effect=project):
            response = self.generate()
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.state(), before)
