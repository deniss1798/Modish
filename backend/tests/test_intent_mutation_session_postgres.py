"""Atomic refine/replace races through HTTP on disposable PostgreSQL."""
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

from app.api import recommendations as feedback_api, shopping_intents as intent_api
from app.api.deps import get_db
from app.models import FitProfile, Product, ShoppingIntent, User
from app.services import intent_mutation_session as mutations
from app.services.recommendation_revision import profile_input_mutation
from isolated_postgres import create_isolated_postgres
import test_intent_outfit_create_session as fixtures


@unittest.skipUnless(os.getenv('MODISH_ISOLATED_PG_RUNTIME'), 'Opt-in disposable PostgreSQL fixture')
class IntentMutationSessionPostgresTests(unittest.TestCase):
    def setUp(self):
        isolated = create_isolated_postgres(self, namespace='intent_mutation')
        self.engine = isolated.engine
        with Session(self.engine, autoflush=False) as db:
            self.user_id = fixtures.seed_outfit_catalogue(db)
        app, self.auth_reads = FastAPI(), []
        app.include_router(intent_api.router)
        app.include_router(feedback_api.router)

        def database(request: Request):
            with Session(self.engine, autoflush=False) as db:
                if request.url.path.endswith(('/refine', '/replace')):
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

    def create(self, *, outfit=False):
        response = self.client.post('/shopping-intents', json={
            'text': fixtures.OUTFIT_TEXT if outfit else 'Рубашки до 6000',
            'request_id': 'mutation-outfit-stable-key' if outfit else 'mutation-single-stable-key',
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['outfits' if outfit else 'products'])
        return response.json()

    def refine(self, current):
        return self.client.post(f"/shopping-intents/{current['id']}/refine", json={
            'expected_version': current['version'], 'text': 'Дешевле',
        })

    def replace(self, current):
        return self.client.post(f"/shopping-intents/{current['id']}/outfits/{current['outfits'][0]['id']}/replace",
            json={'expected_version': current['version'], 'slot': 'shoes'})

    def state(self, intent_id):
        with Session(self.engine) as db:
            row = db.get(ShoppingIntent, intent_id)
            return row.version, deepcopy(row.constraints_json), deepcopy(row.context_json), row.active, row.updated_at

    def metrics(self, intent_id):
        with Session(self.engine) as db:
            return fixtures.metric_counts(db, intent_id)

    def like(self, product_id):
        return self.client.post('/recommendations/events', json={
            'product_id': product_id, 'event_type': 'like', 'meta': {'surface': 'feed'},
        })

    @contextmanager
    def ranking_barrier(self, *, outfit=False):
        ready, release, calls = threading.Event(), threading.Event(), []
        attribute = 'build_intent_outfits' if outfit else 'item_results'
        original = getattr(mutations, attribute)

        def rank(db, *args, **kwargs):
            self.assertTrue(db.info['recommendation_read_only'])
            self.assertNotIn('_recommendation_owner_mutations', db.info)
            self.assertEqual(db.scalar(text('SHOW transaction_read_only')), 'on')
            self.assertEqual(db.scalar(text('SHOW transaction_isolation')), 'repeatable read')
            rows = original(db, *args, **kwargs)
            calls.append(rows)
            if len(calls) == 1:
                self.assertTrue(rows)
                ready.set()
                if not release.wait(4):
                    raise AssertionError('Missing deterministic mutation ranking release')
            return rows

        with patch.object(mutations, attribute, side_effect=rank):
            try:
                yield ready, release, calls
            finally:
                release.set()

    def test_refine_keeps_version_one_until_publish_and_feedback_finishes_during_rank(self):
        current = self.create()
        before = self.state(current['id'])
        with self.ranking_barrier() as (ready, release, calls):
            with ThreadPoolExecutor(max_workers=2) as executor:
                delivery = executor.submit(self.refine, current)
                try:
                    self.assertTrue(ready.wait(4))
                    self.assertFalse(self.auth_reads[0].in_transaction())
                    self.assertEqual(len(self.auth_reads[0].identity_map), 0)
                    self.assertEqual(self.state(current['id']), before)
                    feedback = executor.submit(self.like, current['products'][0]['product']['id']).result(timeout=2)
                    self.assertEqual(feedback.status_code, 200, feedback.text)
                    self.assertEqual(self.state(current['id']), before)
                    self.assertEqual(self.metrics(current['id'])['intent_updated'], 0)
                finally:
                    release.set()
                response = delivery.result(timeout=5)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(calls), 2)
        self.assertEqual(response.json()['version'], 2)
        self.assertEqual(response.json()['constraints']['budget_max'], 4800)
        self.assertTrue(response.json()['products'])
        counts = self.metrics(current['id'])
        self.assertEqual(counts['intent_updated'], 1)
        self.assertEqual(counts['intent_results_generated'], 2)

    def test_concurrent_expected_version_one_refines_have_one_winner_without_double_cheaper(self):
        current = self.create()
        with self.ranking_barrier() as (ready, release, calls):
            with ThreadPoolExecutor(max_workers=2) as executor:
                earlier = executor.submit(self.refine, current)
                try:
                    self.assertTrue(ready.wait(4))
                    winner = executor.submit(self.refine, current).result(timeout=2)
                    self.assertEqual(winner.status_code, 200, winner.text)
                    self.assertEqual(winner.json()['version'], 2)
                finally:
                    release.set()
                stale = earlier.result(timeout=5)
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(len(calls), 2, 'Intent CAS conflict must not launch a third heavy rank')
        self.assertEqual(self.state(current['id'])[0], 2)
        self.assertEqual(self.state(current['id'])[1]['budget_max'], 4800)
        counts = self.metrics(current['id'])
        self.assertEqual(counts['intent_updated'], 1)
        self.assertEqual(counts['intent_results_generated'], 2)
        self.assertEqual(counts['intent_created'], 1)

    def test_replace_retries_current_feedback_fit_and_locked_price_without_changing_locked_ids(self):
        current = self.create(outfit=True)
        original = current['outfits'][0]
        locked_top = original['items']['top']
        old_price = Decimal(str(original['products']['top']['price']))
        before = self.state(current['id'])

        def update_inputs():
            feedback = self.like(locked_top)
            self.assertEqual(feedback.status_code, 200, feedback.text)
            with Session(self.engine, autoflush=False) as db:
                with profile_input_mutation(db, self.user_id):
                    fit = db.scalar(select(FitProfile).where(FitProfile.user_id == self.user_id))
                    fit.height_cm = 171
                    db.get(Product, locked_top).price = old_price + Decimal('0.01')
                db.commit()

        with self.ranking_barrier(outfit=True) as (ready, release, calls):
            with ThreadPoolExecutor(max_workers=2) as executor:
                delivery = executor.submit(self.replace, current)
                try:
                    self.assertTrue(ready.wait(4))
                    self.assertFalse(self.auth_reads[0].in_transaction())
                    self.assertEqual(self.state(current['id']), before)
                    executor.submit(update_inputs).result(timeout=2)
                    self.assertEqual(self.state(current['id']), before)
                finally:
                    release.set()
                response = delivery.result(timeout=5)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(calls), 2)
        result = response.json()
        self.assertEqual(result['version'], 2)
        fixtures.assert_full_outfits(self, result, forbidden={original['items']['shoes']})
        for outfit in result['outfits']:
            self.assertEqual(outfit['items']['top'], original['items']['top'])
            self.assertEqual(outfit['items']['bottom'], original['items']['bottom'])
            self.assertEqual(Decimal(str(outfit['products']['top']['price'])), old_price + Decimal('0.01'))
        counts = self.metrics(current['id'])
        self.assertEqual(counts['outfit_item_replaced'], 1)
        self.assertEqual(counts['intent_updated'], 1)
        self.assertEqual(counts['intent_results_generated'], 2)

    def test_invalid_locked_sku_after_prepare_refuses_replace_without_accepting_any_edit(self):
        current = self.create(outfit=True)
        locked_top = current['outfits'][0]['items']['top']
        before, counts_before = self.state(current['id']), self.metrics(current['id'])

        def invalidate():
            with Session(self.engine, autoflush=False) as db:
                with profile_input_mutation(db, self.user_id):
                    db.scalar(select(FitProfile).where(FitProfile.user_id == self.user_id)).budget_max = 5000
                    db.get(Product, locked_top).price = Decimal('6000.99')
                db.commit()

        with self.ranking_barrier(outfit=True) as (ready, release, calls), \
                patch.object(mutations, '_prepare', wraps=mutations._prepare) as preparing:
            with ThreadPoolExecutor(max_workers=2) as executor:
                delivery = executor.submit(self.replace, current)
                try:
                    self.assertTrue(ready.wait(4))
                    executor.submit(invalidate).result(timeout=2)
                finally:
                    release.set()
                response = delivery.result(timeout=5)
        self.assertIn(response.status_code, (400, 409), response.text)
        self.assertEqual(preparing.call_count, 2)
        self.assertEqual(len(calls), 1, 'Changed original locked outfit fails admission before recomposition')
        self.assertEqual(self.state(current['id']), before)
        self.assertEqual(self.metrics(current['id']), counts_before)

    def test_close_between_phases_wins_and_mutation_publisher_returns_410_without_update(self):
        current = self.create()
        before = self.state(current['id'])
        with self.ranking_barrier() as (ready, release, calls):
            with ThreadPoolExecutor(max_workers=2) as executor:
                delivery = executor.submit(self.refine, current)
                try:
                    self.assertTrue(ready.wait(4))
                    closed = executor.submit(self.client.post, f"/shopping-intents/{current['id']}/close", json={
                        'expected_version': 1, 'outcome': 'abandoned',
                    }).result(timeout=2)
                    self.assertEqual(closed.status_code, 200, closed.text)
                    after_close = self.state(current['id'])
                    self.assertEqual(after_close[0], 1)
                    self.assertFalse(after_close[3])
                finally:
                    release.set()
                response = delivery.result(timeout=5)
        self.assertEqual(response.status_code, 410, response.text)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.state(current['id']), after_close)
        self.assertEqual(after_close[1], before[1])
        self.assertEqual(after_close[2]['product_ids'], before[2]['product_ids'])
        counts = self.metrics(current['id'])
        self.assertEqual(counts['intent_updated'], 0)
        self.assertEqual(counts['intent_results_generated'], 1)
        self.assertEqual(counts['intent_abandoned'], 1)
