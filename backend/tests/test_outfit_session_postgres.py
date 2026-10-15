"""Standalone outfit HTTP publication races on disposable PostgreSQL only."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
import os
import threading
import unittest
from unittest.mock import patch

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api import outfits as outfit_api, recommendations as feedback_api
from app.api.deps import get_db
from app.models import MetricEvent, Outfit, TasteProfile, User
from app.services import outfit_session as outfits
from isolated_postgres import create_isolated_postgres
import test_outfit_session as fixtures


@unittest.skipUnless(os.getenv('MODISH_ISOLATED_PG_RUNTIME'), 'Opt-in disposable PostgreSQL fixture')
class OutfitSessionPostgresTests(unittest.TestCase):
    def setUp(self):
        isolated = create_isolated_postgres(self, namespace='outfit_refresh')
        self.engine = isolated.engine
        with Session(self.engine, autoflush=False) as db:
            self.user_id = fixtures.seed_outfit_refresh_catalogue(db)
        app, self.auth_reads = FastAPI(), []
        app.include_router(outfit_api.router)
        app.include_router(feedback_api.router)

        def database(request: Request):
            with Session(self.engine, autoflush=False) as db:
                if request.url.path == '/outfits/generate':
                    self.auth_reads.append(db)
                yield db

        def authenticated_owner(credentials, db):
            return db.get(User, self.user_id)

        app.dependency_overrides[get_db] = database
        for api in (outfit_api, feedback_api):
            auth = patch.object(api, 'user_from_token', side_effect=authenticated_owner)
            auth.start()
            self.addCleanup(auth.stop)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def generate(self):
        return self.client.post('/outfits/generate', params={'count': 1, 'scenario': 'daily'})

    def taste(self):
        with Session(self.engine) as db:
            row = db.scalar(select(TasteProfile).where(TasteProfile.user_id == self.user_id))
            return deepcopy({column.key: getattr(row, column.key) for column in row.__table__.columns})

    def revision(self):
        with Session(self.engine) as db:
            return db.get(User, self.user_id).recommendation_revision

    def history(self):
        with Session(self.engine) as db:
            return deepcopy([row.meta_json for row in db.scalars(select(MetricEvent).where(
                MetricEvent.user_id == self.user_id, MetricEvent.name == 'outfit_rotation:daily'))])

    @contextmanager
    def ranking_barrier(self):
        ready, release, calls = threading.Event(), threading.Event(), []
        original = outfits.engine.prepare_outfit_batch

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
                    raise AssertionError('Missing deterministic standalone outfit ranking release')
            return batch

        with patch.object(outfits.engine, 'prepare_outfit_batch', side_effect=rank):
            try:
                yield ready, release, calls
            finally:
                release.set()

    def test_feedback_commits_during_read_only_rank_and_disliked_finalist_is_not_published(self):
        with self.ranking_barrier() as (ready, release, calls):
            with ThreadPoolExecutor(max_workers=2) as executor:
                delivery = executor.submit(self.generate)
                try:
                    self.assertTrue(ready.wait(4))
                    self.assertFalse(self.auth_reads[0].in_transaction())
                    self.assertEqual(len(self.auth_reads[0].identity_map), 0)
                    self.assertEqual(self.history(), [])
                    product_id = calls[0][0]['shoes']
                    feedback = executor.submit(self.client.post, '/recommendations/events', json={
                        'product_id': product_id, 'event_type': 'dislike', 'meta': {'surface': 'feed'},
                    }).result(timeout=2)
                    self.assertEqual(feedback.status_code, 200, feedback.text)
                    self.assertEqual(self.history(), [])
                finally:
                    release.set()
                response = delivery.result(timeout=5)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(calls), 2)
        fixtures.assert_complete_batch(self, response.json(), forbidden={product_id})
        self.assertEqual(len(self.history()), 1)
        with Session(self.engine) as db:
            self.assertEqual(set(db.scalars(select(Outfit.id))), {row['id'] for row in response.json()})

    def test_concurrent_refresh_retries_against_winning_rotation_without_lost_history_or_taste_training(self):
        before_revision, before_taste = self.revision(), self.taste()
        with self.ranking_barrier() as (ready, release, calls):
            with ThreadPoolExecutor(max_workers=2) as executor:
                earlier = executor.submit(self.generate)
                try:
                    self.assertTrue(ready.wait(4))
                    winner = executor.submit(self.generate).result(timeout=2)
                    self.assertEqual(winner.status_code, 200, winner.text)
                    fixtures.assert_complete_batch(self, winner.json())
                    self.assertEqual(len(self.history()), 1)
                    self.assertEqual(self.revision(), before_revision + 1)
                finally:
                    release.set()
                retried = earlier.result(timeout=5)
        self.assertEqual(retried.status_code, 200, retried.text)
        self.assertEqual(len(calls), 3, 'One winner plus one bounded rerank of the stale refresh')
        fixtures.assert_complete_batch(self, retried.json())
        winner_ids = set(winner.json()[0]['items'].values())
        retried_ids = set(retried.json()[0]['items'].values())
        self.assertTrue(winner_ids.isdisjoint(retried_ids), 'Fresh stock permits entirely different garments')
        history = self.history()
        self.assertEqual(len(history), 2)
        for response in (winner, retried):
            identifiers = {'id:' + pid for pid in response.json()[0]['items'].values()}
            self.assertTrue(any(identifiers.issubset(set(keys)) for event in history for keys in event['outfits']))
        self.assertEqual(self.revision(), before_revision + 2)
        self.assertEqual(self.taste(), before_taste)
        with Session(self.engine) as db:
            self.assertEqual(set(db.scalars(select(Outfit.id))), {row['id'] for row in retried.json()})

    def test_save_between_phases_finishes_before_rank_release_and_saved_row_survives_retry(self):
        first = self.generate()
        self.assertEqual(first.status_code, 200, first.text)
        original_id = first.json()[0]['id']
        before_revision = self.revision()
        with self.ranking_barrier() as (ready, release, calls):
            with ThreadPoolExecutor(max_workers=2) as executor:
                delivery = executor.submit(self.generate)
                try:
                    self.assertTrue(ready.wait(4))
                    saved = executor.submit(self.client.post, '/outfits/save', params={'outfit_id': original_id}).result(timeout=2)
                    self.assertEqual(saved.status_code, 200, saved.text)
                    self.assertEqual(self.revision(), before_revision + 1)
                    with Session(self.engine) as db:
                        self.assertTrue(db.get(Outfit, original_id).is_saved)
                finally:
                    release.set()
                response = delivery.result(timeout=5)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(calls), 2)
        fixtures.assert_complete_batch(self, response.json())
        self.assertEqual(self.revision(), before_revision + 2)
        self.assertEqual(len(self.history()), 2)
        with Session(self.engine) as db:
            self.assertTrue(db.get(Outfit, original_id).is_saved)
            self.assertEqual(set(db.scalars(select(Outfit.id))), {original_id, response.json()[0]['id']})
