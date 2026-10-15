"""Actual single-item HTTP concurrency on the disposable PostgreSQL fixture."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import os
import threading
import unittest
from unittest.mock import patch

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select, text
from sqlalchemy.orm import Session

from app.api import recommendations as feedback_api, shopping_intents as intent_api
from app.api.deps import get_db
from app.models import FitProfile, MetricEvent, ProductSource, ShoppingIntent, User
from app.services import activation_service as activation, intent_session
from app.services.recommendation_revision import profile_input_mutation
from isolated_postgres import create_isolated_postgres
import test_closed_alpha_first_session as fixtures


@unittest.skipUnless(os.getenv('MODISH_ISOLATED_PG_RUNTIME'), 'Opt-in disposable PostgreSQL fixture')
class IntentSessionPostgresTests(unittest.TestCase):
    def setUp(self):
        isolated = create_isolated_postgres(self, namespace='intent', pool_size=4)
        self.admin, self.engine = isolated.admin, isolated.engine
        fixture = fixtures.ClosedAlphaFirstSessionTests()
        with Session(self.engine, autoflush=False) as db:
            fixture.db, fixture.size, fixture.maximum = db, 'M', Decimal('10000')
            fixture.user = User(id='intent-owner', email='intent-pg@example.test', password_hash='unused')
            db.add(fixture.user)
            db.flush()
            fixture.session = activation.configure(db, fixture.user, gender_target='womenswear', age_group='25-34',
                height=170, clothing_size='M', clothing_size_system='LETTER', shoe_size='38',
                shoe_size_system='EU', styles=['casual', 'minimalism'], price_segment='mass')
            fixture.good, fixture.bad, fixture.rows = set(), set(), {}
            db.add(ProductSource(id='merchant', code='baon', name='BAON', network='direct'))
            fixture.positive_catalogue(per_category=6)
            for product in fixture.rows.values():
                product.source_id = 'merchant'
            db.flush()
            fixture.warm()
            self.user_id = fixture.user.id
        self.app, self.auth_reads = FastAPI(), []
        self.app.include_router(intent_api.router)
        self.app.include_router(feedback_api.router)

        def database(request: Request):
            with Session(self.engine, autoflush=False) as db:
                if request.method == 'POST' and request.url.path == '/shopping-intents':
                    self.auth_reads.append(db)
                yield db

        def authenticated_owner(credentials, db):
            return db.get(User, self.user_id)

        self.app.dependency_overrides[get_db] = database
        for api in (intent_api, feedback_api):
            auth = patch.object(api, 'user_from_token', side_effect=authenticated_owner)
            auth.start()
            self.addCleanup(auth.stop)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    def create(self, key='intent-stable-request-key', text='Рубашки до 7000'):
        return self.client.post('/shopping-intents', json={'text': text, 'request_id': key})

    def metric_counts(self, intent_id):
        with Session(self.engine) as db:
            return Counter(row.name for row in db.scalars(select(MetricEvent))
                if (row.meta_json or {}).get('intent_id') == intent_id)

    def persisted(self, key='intent-stable-request-key'):
        with Session(self.engine) as db:
            intent = db.scalar(select(ShoppingIntent).where(ShoppingIntent.user_id == self.user_id,
                ShoppingIntent.request_id == key))
            return intent.id, dict(intent.context_json), intent.version

    def assert_once(self, intent_id):
        counts = self.metric_counts(intent_id)
        for name in ('intent_created', 'intent_parsed', 'intent_results_generated', 'availability_filtered', 'size_filtered'):
            self.assertEqual(counts[name], 1, (name, counts))

    @contextmanager
    def ranking_barrier(self):
        ready, release, calls, selected = threading.Event(), threading.Event(), [], []
        original = intent_session.item_results

        def rank(db, *args, **kwargs):
            self.assertEqual(db.scalar(text('SHOW transaction_read_only')), 'on')
            self.assertEqual(db.scalar(text('SHOW transaction_isolation')), 'repeatable read')
            rows = original(db, *args, **kwargs)
            calls.append([row['product']['id'] for row in rows])
            if len(calls) == 1:
                self.assertTrue(rows)
                selected.extend(calls[0])
                ready.set()
                if not release.wait(4):
                    raise AssertionError('Missing deterministic Intent ranking release')
            return rows

        with patch.object(intent_session, 'item_results', side_effect=rank):
            try:
                yield ready, release, calls, selected
            finally:
                release.set()

    def test_feedback_http_commits_during_ranking_then_current_revision_is_published_once(self):
        with self.ranking_barrier() as (ready, release, calls, selected):
            with ThreadPoolExecutor(max_workers=2) as executor:
                delivery = executor.submit(self.create)
                try:
                    self.assertTrue(ready.wait(4))
                    self.assertFalse(self.auth_reads[0].in_transaction())
                    self.assertEqual(len(self.auth_reads[0].identity_map), 0)
                    feedback = executor.submit(self.client.post, '/recommendations/events', json={
                        'product_id': selected[0], 'event_type': 'dislike', 'meta': {'surface': 'feed'},
                    }).result(timeout=2)
                    self.assertEqual(feedback.status_code, 200, feedback.text)
                    intent_id, context, _ = self.persisted()
                    self.assertEqual(context['generation_v1']['state'], 'pending')
                    self.assertEqual(self.metric_counts(intent_id)['intent_results_generated'], 0)
                finally:
                    release.set()
                response = delivery.result(timeout=5)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['products'])
        self.assertEqual(len(calls), 2)
        self.assert_once(response.json()['id'])

    def test_distinct_keys_can_finish_concurrently_without_waiting_for_first_ranking(self):
        with self.ranking_barrier() as (ready, release, calls, selected):
            with ThreadPoolExecutor(max_workers=2) as executor:
                earlier = executor.submit(self.create)
                try:
                    self.assertTrue(ready.wait(4))
                    later = executor.submit(self.create, 'second-independent-key').result(timeout=2)
                    self.assertEqual(later.status_code, 200, later.text)
                    self.assertTrue(later.json()['products'])
                finally:
                    release.set()
                first = earlier.result(timeout=5)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertNotEqual(first.json()['id'], later.json()['id'])
        self.assertEqual(len(calls), 2)
        self.assert_once(first.json()['id'])
        self.assert_once(later.json()['id'])

    def test_pending_same_key_and_mismatch_are_immediate_then_replay_scans_only_issued_ids(self):
        with self.ranking_barrier() as (ready, release, calls, selected):
            with ThreadPoolExecutor(max_workers=2) as executor:
                earlier = executor.submit(self.create)
                try:
                    self.assertTrue(ready.wait(4))
                    duplicate = executor.submit(self.create).result(timeout=2)
                    self.assertEqual(duplicate.status_code, 409, duplicate.text)
                    self.assertEqual(duplicate.headers.get('Retry-After'), '2')
                    mismatch = executor.submit(self.create, text='Брюки до 7000').result(timeout=2)
                    self.assertEqual(mismatch.status_code, 409, mismatch.text)
                    self.assertIn('Ключ', mismatch.json()['detail'])
                    self.assertEqual(len(calls), 1)
                    intent_id, context, _ = self.persisted()
                    self.assertNotIn('result_version', context)
                    self.assertEqual(self.metric_counts(intent_id)['intent_results_generated'], 0)
                finally:
                    release.set()
                first = earlier.result(timeout=5)
        self.assertEqual(first.status_code, 200, first.text)
        statements = []

        def record(connection, cursor, statement, *args):
            if 'FROM products' in statement:
                statements.append(statement)

        event.listen(self.engine, 'before_cursor_execute', record)
        try:
            with patch.object(intent_session, 'item_results', side_effect=AssertionError('Replay reranked catalogue')):
                replay = self.create()
        finally:
            event.remove(self.engine, 'before_cursor_execute', record)
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json(), first.json())
        self.assertTrue(statements, 'Replay should revalidate the issued products')
        self.assertTrue(all('WHERE products.id IN' in sql or 'WHERE products.id =' in sql for sql in statements), statements)
        self.assert_once(first.json()['id'])
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(ShoppingIntent)), 1)

    def test_expired_claim_takeover_rejects_old_worker_without_overwriting_ready_result(self):
        clock = [datetime.now(timezone.utc)]
        with patch.object(intent_session, '_now', side_effect=lambda: clock[0]), \
                self.ranking_barrier() as (ready, release, calls, selected):
            with ThreadPoolExecutor(max_workers=2) as executor:
                earlier = executor.submit(self.create)
                try:
                    self.assertTrue(ready.wait(4))
                    intent_id, pending, version = self.persisted()
                    self.assertEqual(pending['generation_v1']['generation'], 1)
                    clock[0] += timedelta(seconds=intent_session.PENDING_LEASE_SECONDS + 1)
                    winner = executor.submit(self.create).result(timeout=2)
                    self.assertEqual(winner.status_code, 200, winner.text)
                    winning_id, finished, winning_version = self.persisted()
                    self.assertEqual(winning_id, intent_id)
                    self.assertEqual(winning_version, version)
                    self.assertEqual(finished['generation_v1']['generation'], 2)
                    self.assertEqual(finished['generation_v1']['state'], 'ready')
                finally:
                    release.set()
                stale = earlier.result(timeout=5)
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(len(calls), 2)
        self.assertTrue(winner.json()['products'])
        self.assertEqual(self.persisted()[1], finished)
        self.assert_once(intent_id)

    def test_repeated_revision_conflict_has_no_partial_results_metrics_or_issued_ids(self):
        original, calls = intent_session._prepare, []

        def prepare(*args, **kwargs):
            proposal = original(*args, **kwargs)
            calls.append(proposal)
            with Session(self.engine, autoflush=False) as db:
                with profile_input_mutation(db, self.user_id):
                    fit = db.scalar(select(FitProfile).where(FitProfile.user_id == self.user_id))
                    fit.budget_max -= 1
                db.commit()
            return proposal

        with patch.object(intent_session, '_prepare', side_effect=prepare):
            response = self.create()
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(len(calls), 2)
        intent_id, context, _ = self.persisted()
        self.assertNotIn('result_version', context)
        self.assertFalse(context.get('product_ids'))
        self.assertEqual(self.metric_counts(intent_id)['intent_results_generated'], 0)
        self.assertEqual(self.metric_counts(intent_id)['size_filtered'], 0)
        self.assertEqual(self.metric_counts(intent_id)['availability_filtered'], 0)
        # Release makes the same durable request recoverable immediately.
        retry = self.create()
        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertEqual(retry.json()['id'], intent_id)
        self.assertTrue(retry.json()['products'])
        self.assert_once(intent_id)

    def test_fit_change_reranks_and_returns_nonempty_only_current_budget_products(self):
        original, calls = intent_session._prepare, []
        cheap_ids = {f'new-cheap-shirt-{index}' for index in range(5)}

        def prepare(*args, **kwargs):
            proposal = original(*args, **kwargs)
            calls.append(proposal)
            if len(calls) == 1:
                with Session(self.engine, autoflush=False) as db:
                    with profile_input_mutation(db, self.user_id):
                        fit = db.scalar(select(FitProfile).where(FitProfile.user_id == self.user_id))
                        fit.budget_max = 500
                        fixture = fixtures.ClosedAlphaFirstSessionTests()
                        fixture.db, fixture.size, fixture.rows = db, 'M', {}
                        for sku in sorted(cheap_ids):
                            fixture.product(sku, source_id='merchant', price=Decimal('399'))
                    db.commit()
            return proposal

        with patch.object(intent_session, '_prepare', side_effect=prepare):
            response = self.create()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(calls), 2)
        products = response.json()['products']
        self.assertTrue(products)
        self.assertTrue(cheap_ids & {row['product']['id'] for row in products})
        self.assertTrue(all(Decimal(str(row['product']['price'])) <= 500 for row in products))
        self.assert_once(response.json()['id'])
