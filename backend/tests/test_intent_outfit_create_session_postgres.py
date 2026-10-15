"""Outfit-create HTTP concurrency on the fenced disposable PostgreSQL only."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from decimal import Decimal
import os
import threading
import unittest
from unittest.mock import patch

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.api import recommendations as feedback_api, shopping_intents as intent_api
from app.api.deps import get_db
from app.models import Outfit, Product, ShoppingIntent, User
from app.services import intent_session as intents, shopping_intent_service as service
from isolated_postgres import create_isolated_postgres
import test_intent_outfit_create_session as fixtures


@unittest.skipUnless(os.getenv('MODISH_ISOLATED_PG_RUNTIME'), 'Opt-in disposable PostgreSQL fixture')
class IntentOutfitCreateSessionPostgresTests(unittest.TestCase):
    def setUp(self):
        isolated = create_isolated_postgres(self, namespace='outfit_create')
        self.engine = isolated.engine
        with Session(self.engine, autoflush=False) as db:
            self.user_id = fixtures.seed_outfit_catalogue(db)
        app, self.auth_reads = FastAPI(), []
        app.include_router(intent_api.router)
        app.include_router(feedback_api.router)

        def database(request: Request):
            with Session(self.engine, autoflush=False) as db:
                if request.method == 'POST' and request.url.path == '/shopping-intents':
                    self.auth_reads.append(db)
                yield db

        def authenticated_owner(credentials, db):
            return db.get(User, self.user_id)

        app.dependency_overrides[get_db] = database
        for api in (intent_api, feedback_api):
            auth = patch.object(api, 'user_from_token', side_effect=authenticated_owner)
            auth.start()
            self.addCleanup(auth.stop)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def create(self, key='outfit-pg-stable-request-key'):
        return self.client.post('/shopping-intents', json={'text': fixtures.OUTFIT_TEXT, 'request_id': key})

    def assert_once(self, response):
        with Session(self.engine) as db:
            counts = fixtures.metric_counts(db, response['id'])
            for name in ('intent_created', 'intent_parsed', 'intent_results_generated', 'availability_filtered', 'size_filtered'):
                self.assertEqual(counts[name], 1, (name, counts))
            self.assertEqual(counts['outfit_generated'], len(response['outfits']))

    @contextmanager
    def ranking_barrier(self):
        ready, release, calls = threading.Event(), threading.Event(), []
        original = intents.build_intent_outfits

        def rank(db, *args, **kwargs):
            self.assertTrue(db.info['recommendation_read_only'])
            self.assertEqual(db.scalar(text('SHOW transaction_read_only')), 'on')
            self.assertEqual(db.scalar(text('SHOW transaction_isolation')), 'repeatable read')
            rows = original(db, *args, **kwargs)
            calls.append(rows)
            if len(calls) == 1:
                self.assertTrue(rows)
                ready.set()
                if not release.wait(4):
                    raise AssertionError('Missing deterministic outfit ranking release')
            return rows

        with patch.object(intents, 'build_intent_outfits', side_effect=rank):
            try:
                yield ready, release, calls
            finally:
                release.set()

    def test_feedback_api_finishes_during_read_only_outfit_rank_and_current_result_publishes_once(self):
        with self.ranking_barrier() as (ready, release, calls):
            with ThreadPoolExecutor(max_workers=2) as executor:
                delivery = executor.submit(self.create)
                try:
                    self.assertTrue(ready.wait(4))
                    self.assertFalse(self.auth_reads[0].in_transaction())
                    self.assertEqual(len(self.auth_reads[0].identity_map), 0)
                    product_id = calls[0][0]['items']['top']
                    feedback = executor.submit(self.client.post, '/recommendations/events', json={
                        'product_id': product_id, 'event_type': 'like', 'meta': {'surface': 'feed'},
                    }).result(timeout=2)
                    self.assertEqual(feedback.status_code, 200, feedback.text)
                    with Session(self.engine) as db:
                        pending = db.scalar(select(ShoppingIntent))
                        self.assertEqual(fixtures.metric_counts(db, pending.id)['intent_results_generated'], 0)
                        self.assertNotIn('result_version', pending.context_json)
                finally:
                    release.set()
                response = delivery.result(timeout=5)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(calls), 2)
        fixtures.assert_full_outfits(self, response.json())
        self.assert_once(response.json())

    def test_finalist_stock_and_price_conflicts_rerank_once_with_complete_honest_outfits(self):
        for kind in ('stock', 'price'):
            with self.subTest(kind=kind):
                original, changed = intents._publish, []

                def publish(sessions, user_id, proposal):
                    if not changed:
                        changed.append(dict(proposal.outfits[0])['shoes'])
                        with sessions() as db:
                            product = db.get(Product, changed[0])
                            if kind == 'stock':
                                product.is_available, product.availability_status = 0, 'out_of_stock'
                            else:
                                product.price = Decimal('6000.99')
                            db.commit()
                    return original(sessions, user_id, proposal)

                with patch.object(intents, '_publish', side_effect=publish) as publishing:
                    response = self.create(key=f'outfit-pg-changed-{kind}-key')
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(publishing.call_count, 2)
                fixtures.assert_full_outfits(self, response.json(), forbidden=changed)
                self.assert_once(response.json())

    def test_same_key_pending_returns_before_rank_release_and_ready_replay_never_recomposes(self):
        with self.ranking_barrier() as (ready, release, calls):
            with ThreadPoolExecutor(max_workers=2) as executor:
                delivery = executor.submit(self.create)
                try:
                    self.assertTrue(ready.wait(4))
                    duplicate = executor.submit(self.create).result(timeout=2)
                    self.assertEqual(duplicate.status_code, 409, duplicate.text)
                    self.assertEqual(duplicate.headers.get('Retry-After'), '2')
                    self.assertEqual(len(calls), 1)
                finally:
                    release.set()
                first = delivery.result(timeout=5)
        self.assertEqual(first.status_code, 200, first.text)
        with patch.object(intents, 'build_intent_outfits', side_effect=AssertionError('POST replay recomposed')), \
                patch.object(service, 'build_intent_outfits', side_effect=AssertionError('GET replay recomposed')):
            replay = self.create()
            read = self.client.get(f"/shopping-intents/{first.json()['id']}")
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(read.status_code, 200, read.text)
        self.assertEqual(replay.json(), first.json())
        self.assertEqual(read.json(), first.json())
        fixtures.assert_full_outfits(self, replay.json())
        self.assert_once(first.json())

    def test_repeated_evidence_conflicts_have_no_partial_outfit_publication(self):
        original = intents._publish

        def publish(sessions, user_id, proposal):
            with sessions() as db:
                db.get(Product, proposal.finalists[0].product_id).price += Decimal('0.01')
                db.commit()
            return original(sessions, user_id, proposal)

        with patch.object(intents, '_publish', side_effect=publish) as publishing:
            response = self.create()
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(publishing.call_count, 2)
        with Session(self.engine) as db:
            row = db.scalar(select(ShoppingIntent))
            self.assertNotIn('result_version', row.context_json)
            self.assertFalse(row.context_json.get('outfits'))
            counts = fixtures.metric_counts(db, row.id)
            self.assertEqual(counts['intent_results_generated'], 0)
            self.assertEqual(counts['outfit_generated'], 0)
            self.assertEqual(db.scalar(select(func.count()).select_from(Outfit)), 0)
