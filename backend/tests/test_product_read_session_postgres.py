"""Personal product GETs release auth/rank reads before current publication."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from decimal import Decimal
import os
import threading
import unittest
from unittest.mock import patch

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api import products as products_api, recommendations as feedback_api
from app.api.deps import get_db
from app.models import FitProfile, Product, User
from app.services import product_read_session as reads
from isolated_postgres import create_isolated_postgres
import test_intent_outfit_create_session as catalogue
import test_product_read_session as fixtures


@unittest.skipUnless(os.getenv('MODISH_ISOLATED_PG_RUNTIME'), 'Opt-in disposable PostgreSQL fixture')
class ProductReadSessionPostgresTests(unittest.TestCase):
    def setUp(self):
        isolated = create_isolated_postgres(self, namespace='product_read')
        self.engine = isolated.engine
        with Session(self.engine, autoflush=False) as db:
            self.user_id = catalogue.seed_outfit_catalogue(db)
            db.scalar(select(FitProfile).where(FitProfile.user_id == self.user_id)).budget_max = 6000
            db.commit()
        app, self.auth_reads = FastAPI(), []
        app.include_router(products_api.router)
        app.include_router(feedback_api.router)

        def database(request: Request):
            with Session(self.engine, autoflush=False) as db:
                if request.method == 'GET':
                    self.auth_reads.append(db)
                yield db

        def authenticated_owner(credentials, db):
            return db.get(User, self.user_id)

        app.dependency_overrides[get_db] = database
        for api in (products_api, feedback_api):
            auth = patch.object(api, 'user_from_token', side_effect=authenticated_owner)
            auth.start()
            self.addCleanup(auth.stop)
        self.client = TestClient(app, headers={'Authorization': 'Bearer SYNTHETIC_PRODUCT_READ_TOKEN'})
        self.addCleanup(self.client.close)

    def get_results(self, operation):
        if operation == 'search':
            return self.client.get('/products', params={'q': 'рубашки', 'limit': 50})
        return self.client.get('/products/good-top-0/complements', params={'limit': 12})

    def personal_snapshot(self):
        with Session(self.engine) as db:
            return (db.get(User, self.user_id).recommendation_revision, {
                model.__tablename__: sorted((deepcopy(dict(row)) for row in
                    db.execute(select(model.__table__)).mappings()),
                    key=lambda row: str(row.get('id', row.get('user_id'))))
                for model in fixtures.PERSONAL_MODELS})

    def assert_results(self, response, operation, *, forbidden=()):
        self.assertEqual(response.status_code, 200, response.text)
        products = response.json()
        self.assertTrue(products, 'The fixed positive catalogue must return real products')
        expected = fixtures.TOP_IDS if operation == 'search' else fixtures.COMPLEMENT_IDS
        self.assertEqual({row['id'] for row in products}, expected - set(forbidden))
        self.assertEqual(len(products), len({row['id'] for row in products}))
        for product in products:
            self.assertEqual(product['gender_target'], 'womenswear')
            self.assertTrue(product['is_available'])
            self.assertEqual(product['available_sizes'], ['38' if product['outfit_slot'] == 'shoes' else 'M'])
            self.assertLessEqual(Decimal(str(product['price'])), Decimal('6000'))
            if operation == 'search':
                self.assertEqual(product['outfit_slot'], 'top')
            else:
                self.assertIn(product['outfit_slot'], {'bottom', 'shoes'})
                self.assertLessEqual(Decimal(str(product['price'])) + Decimal('1200.25'), Decimal('6000'))

    @contextmanager
    def ranking_barrier(self, operation):
        attribute = 'personalized_search' if operation == 'search' else 'complementary_products'
        original = getattr(reads, attribute)
        ready, release, calls = threading.Event(), threading.Event(), []

        def rank(db, user, *args, **kwargs):
            self.assertTrue(db.info['recommendation_read_only'])
            self.assertNotIn('_recommendation_owner_mutations', db.info)
            self.assertEqual(db.scalar(text('SHOW transaction_read_only')), 'on')
            self.assertEqual(db.scalar(text('SHOW transaction_isolation')), 'repeatable read')
            products = original(db, user, *args, **kwargs)
            calls.append((tuple(product.id for product in products), user.recommendation_revision))
            if len(calls) == 1:
                self.assertTrue(products)
                ready.set()
                if not release.wait(4):
                    raise AssertionError('Missing deterministic personalized product GET ranking release')
            return products

        with patch.object(reads, attribute, side_effect=rank):
            try:
                yield ready, release, calls
            finally:
                release.set()

    def _feedback_during_rank(self, operation):
        before = self.personal_snapshot()
        with self.ranking_barrier(operation) as (ready, release, calls):
            with ThreadPoolExecutor(max_workers=2) as executor:
                delivery = executor.submit(self.get_results, operation)
                try:
                    self.assertTrue(ready.wait(4))
                    self.assertFalse(self.auth_reads[0].in_transaction())
                    self.assertEqual(len(self.auth_reads[0].identity_map), 0)
                    self.assertEqual(self.personal_snapshot(), before)
                    feedback = executor.submit(self.client.post, '/recommendations/events', json={
                        'product_id': calls[0][0][0], 'event_type': 'like', 'meta': {'surface': 'feed'},
                    }).result(timeout=2)
                    self.assertEqual(feedback.status_code, 200, feedback.text)
                    after_feedback = self.personal_snapshot()
                    self.assertEqual(after_feedback[0], before[0] + 1)
                    self.assertNotEqual(after_feedback[1]['taste_profiles'], before[1]['taste_profiles'])
                finally:
                    release.set()
                response = delivery.result(timeout=5)
        self.assert_results(response, operation)
        self.assertEqual(len(calls), 2, 'A personal revision conflict allows exactly one new rank')
        self.assertEqual([revision for _, revision in calls], [before[0], after_feedback[0]])
        # Only the actual feedback can train Taste or alter delivery state;
        # search and complements cannot allocate/commit any of these rows.
        self.assertEqual(self.personal_snapshot(), after_feedback)

    def test_search_feedback_http_commits_during_rank_and_current_personal_revision_is_retried(self):
        self._feedback_during_rank('search')

    def test_complements_feedback_http_commits_during_rank_and_current_personal_revision_is_retried(self):
        self._feedback_during_rank('complements')

    def test_anchor_change_is_detected_without_owner_revision_and_retries_to_honest_empty(self):
        before = self.personal_snapshot()

        def change_anchor():
            with Session(self.engine) as db:
                db.get(Product, 'good-top-0').price = Decimal('5900')
                db.commit()

        with self.ranking_barrier('complements') as (ready, release, calls):
            with ThreadPoolExecutor(max_workers=2) as executor:
                delivery = executor.submit(self.get_results, 'complements')
                try:
                    self.assertTrue(ready.wait(4))
                    self.assertNotIn('good-top-0', calls[0][0], 'The unreturned anchor needs independent evidence')
                    executor.submit(change_anchor).result(timeout=2)
                    self.assertEqual(self.personal_snapshot(), before)
                finally:
                    release.set()
                response = delivery.result(timeout=5)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), [], 'Every anchor-plus-complement total now exceeds 6000')
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1][0], ())
        self.assertEqual(self.personal_snapshot(), before)

    def test_search_finalist_stock_change_commits_during_rank_and_never_publishes_old_availability(self):
        before = self.personal_snapshot()

        def remove_stock(product_id):
            with Session(self.engine) as db:
                product = db.get(Product, product_id)
                product.is_available, product.availability_status = 0, 'out_of_stock'
                db.commit()

        with self.ranking_barrier('search') as (ready, release, calls):
            with ThreadPoolExecutor(max_workers=2) as executor:
                delivery = executor.submit(self.get_results, 'search')
                try:
                    self.assertTrue(ready.wait(4))
                    product_id = calls[0][0][0]
                    executor.submit(remove_stock, product_id).result(timeout=2)
                    self.assertEqual(self.personal_snapshot(), before)
                finally:
                    release.set()
                response = delivery.result(timeout=5)
        self.assert_results(response, 'search', forbidden={product_id})
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.personal_snapshot(), before)
