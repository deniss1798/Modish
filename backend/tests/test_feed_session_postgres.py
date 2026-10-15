"""Real concurrency checks, opt-in to the disposable workspace PG fixture.

No application credentials or existing PostgreSQL service are used. Every test
checks the server's data directory before creating its own generated schema.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from decimal import Decimal
import os
import threading
import time
import unittest
from unittest.mock import patch

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select, text
from sqlalchemy.orm import Session

from app.api.catalog_admin import SourceRuleCreate, admin_catalog_source_rule_create
from app.api import products as products_api, recommendations as recommendations_api
from app.api.deps import get_db
from app.models import (
    ActivationSession, FitProfile, Product, ProductImpression, ProductSource,
    RecommendationEventV2, SourceRule, TasteProfile, User, UserProductState, UserTasteFeature,
)
from app.schemas.product_event import EventRequest
from app.services import activation_service as activation
from app.services import feed_session
from app.services.catalog.admitad_bootstrap import _ensure_clothing_rules
from app.services.product_event_service import record_product_event
from app.services.recommendation_revision import profile_input_mutation
import test_closed_alpha_first_session as fixtures
from isolated_postgres import create_isolated_postgres


@unittest.skipUnless(os.getenv('MODISH_ISOLATED_PG_RUNTIME'), 'Opt-in disposable PostgreSQL fixture')
class FeedSessionPostgresTests(unittest.TestCase):
    def setUp(self):
        isolated = create_isolated_postgres(self, namespace='feed')
        self.admin, self.engine, self.schema = isolated.admin, isolated.engine, isolated.schema
        # Reuse the independent fixed SKU labels without importing a TestCase
        # into this module's discoverable namespace or bypassing warm-up gates.
        fixture = fixtures.ClosedAlphaFirstSessionTests()
        with Session(self.engine, autoflush=False) as db:
            fixture.db, fixture.size, fixture.maximum = db, 'M', Decimal('10000')
            fixture.user = User(id='first-session-user', email='first-session@example.test', password_hash='unused')
            db.add(fixture.user)
            db.flush()
            fixture.session = activation.configure(db, fixture.user, gender_target='womenswear', age_group='25-34',
                height=170, clothing_size='M', clothing_size_system='LETTER', shoe_size='38',
                shoe_size_system='EU', styles=['casual', 'minimalism'], price_segment='mass')
            fixture.good, fixture.bad, fixture.rows = set(), set(), {}
            source = ProductSource(id='merchant', code='baon', name='BAON', network='direct')
            db.add(source)
            fixture.positive_catalogue(per_category=6)
            for product in fixture.rows.values():
                product.source_id = source.id
            db.flush()
            fixture.warm()
            self.user_id = fixture.user.id

    def feed(self):
        return feed_session.build_feed_response(self.engine, self.user_id, limit=5)

    def feedback(self, product_id):
        with Session(self.engine, autoflush=False) as db:
            record_product_event(db, db.get(User, self.user_id),
                EventRequest(product_id=product_id, event_type='dislike', meta={'surface': 'feed'}))

    def add_product(self, sku, **changes):
        with Session(self.engine) as db:
            fixture = fixtures.ClosedAlphaFirstSessionTests()
            fixture.db, fixture.size, fixture.rows = db, 'M', {}
            fixture.product(sku, source_id='merchant', **changes)
            db.commit()
        return sku

    def assert_backend_waits_on_lock(self, pid, completed):
        deadline, actually_waiting = time.monotonic() + 2, False
        with self.admin.connect() as observer:
            while time.monotonic() < deadline:
                observer.execute(text('SELECT pg_stat_clear_snapshot()'))
                wait = observer.scalar(text('SELECT wait_event_type FROM pg_stat_activity WHERE pid=:pid'),
                    {'pid': pid})
                if wait == 'Lock':
                    actually_waiting = True
                    break
                if completed.wait(.02):
                    break
        self.assertTrue(actually_waiting, 'Writer did not wait on a PostgreSQL row lock')
        self.assertFalse(completed.is_set())

    def assert_delivery(self, response, *, forbidden=()):
        ids = [row['product']['id'] for row in response]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(set(ids).isdisjoint(forbidden))
        with Session(self.engine) as db:
            impressions = db.scalars(select(ProductImpression)).all()
            self.assertEqual(len(impressions), len(ids))
            self.assertEqual({row.product_id for row in impressions}, set(ids))
            issued = db.get(ActivationSession, self.user_id).issued_feed_ids
            self.assertEqual(set(issued), set(ids))

    @contextmanager
    def ranking_barrier(self):
        ready, release = threading.Event(), threading.Event()
        calls, selected = [], []
        original = feed_session.generate_feed

        def rank(db, *args, **kwargs):
            self.assertTrue(kwargs['read_only'])
            self.assertEqual(db.scalar(text('SHOW transaction_read_only')), 'on')
            self.assertEqual(db.scalar(text('SHOW transaction_isolation')), 'repeatable read')
            rows = original(db, *args, **kwargs)
            calls.append(tuple(row.product.id for row in rows))
            if len(calls) == 1:
                self.assertTrue(rows)
                selected.extend(row.product.id for row in rows)
                ready.set()
                if not release.wait(4):
                    raise AssertionError('Missing deterministic ranking release')
            return rows

        with patch.object(feed_session, 'generate_feed', side_effect=rank):
            try:
                yield ready, release, calls, selected
            finally:
                release.set()

    def test_feedback_commits_while_read_only_ranking_is_blocked_then_reranks_once(self):
        with self.ranking_barrier() as (ready, release, calls, selected):
            with ThreadPoolExecutor(max_workers=2) as executor:
                delivery = executor.submit(self.feed)
                try:
                    self.assertTrue(ready.wait(4))
                    executor.submit(self.feedback, selected[0]).result(timeout=2)
                finally:
                    release.set()
                response = delivery.result(timeout=5)
        self.assertEqual(len(calls), 2)
        self.assertTrue(response)
        self.assert_delivery(response, forbidden={selected[0]})

    def test_fit_budget_change_commits_before_ranking_release_and_no_old_budget_is_delivered(self):
        cheap_ids = {f'after-warm-cheap-{index}' for index in range(5)}

        def change_fit():
            with Session(self.engine, autoflush=False) as db:
                with profile_input_mutation(db, self.user_id):
                    fit = db.scalar(select(FitProfile).where(FitProfile.user_id == self.user_id))
                    fit.budget_max = 500
                    fixture = fixtures.ClosedAlphaFirstSessionTests()
                    fixture.db, fixture.size, fixture.rows = db, 'M', {}
                    for sku in sorted(cheap_ids):
                        fixture.product(sku, price=Decimal('399'), source_id='merchant')
                db.commit()

        with self.ranking_barrier() as (ready, release, calls, selected):
            with ThreadPoolExecutor(max_workers=2) as executor:
                delivery = executor.submit(self.feed)
                try:
                    self.assertTrue(ready.wait(4))
                    executor.submit(change_fit).result(timeout=2)
                finally:
                    release.set()
                response = delivery.result(timeout=5)
        self.assertEqual(len(calls), 2)
        self.assertTrue(response, 'The updated budget has new, explicitly eligible unswiped SKUs')
        self.assertTrue(cheap_ids & {row['product']['id'] for row in response})
        self.assertTrue(all(Decimal(str(row['product']['price'])) <= 500 for row in response))
        self.assert_delivery(response, forbidden=set(selected))

    def test_feed_http_releases_auth_session_and_feedback_api_commits_during_ranking(self):
        app, auth_reads = FastAPI(), []
        app.include_router(products_api.router)
        app.include_router(recommendations_api.router)

        def database(request: Request):
            with Session(self.engine, autoflush=False) as db:
                if request.url.path == '/feed':
                    auth_reads.append(db)
                yield db

        def authenticated_owner(credentials, db):
            return db.get(User, self.user_id)

        app.dependency_overrides[get_db] = database
        with TestClient(app) as client, \
                patch.object(products_api, 'user_from_token', side_effect=authenticated_owner), \
                patch.object(recommendations_api, 'user_from_token', side_effect=authenticated_owner), \
                self.ranking_barrier() as (ready, release, calls, selected):
            with ThreadPoolExecutor(max_workers=2) as executor:
                delivery = executor.submit(client.get, '/feed', params={'limit': 5})
                try:
                    self.assertTrue(ready.wait(4))
                    self.assertEqual(len(auth_reads), 1)
                    self.assertFalse(auth_reads[0].in_transaction(), 'Authentication still holds a pooled connection')
                    self.assertEqual(len(auth_reads[0].identity_map), 0)
                    feedback = executor.submit(client.post, '/recommendations/events', json={
                        'product_id': selected[0], 'event_type': 'dislike', 'meta': {'surface': 'feed'},
                    }).result(timeout=2)
                    self.assertEqual(feedback.status_code, 200, feedback.text)
                finally:
                    release.set()
                response = delivery.result(timeout=5)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(calls), 2)
        self.assertTrue(response.json())
        self.assert_delivery(response.json(), forbidden={selected[0]})

    def test_second_revision_conflict_returns_409_without_partial_delivery(self):
        original = feed_session.generate_feed
        calls = []

        def rank(db, *args, **kwargs):
            rows = original(db, *args, **kwargs)
            calls.append([row.product.id for row in rows])
            with Session(self.engine, autoflush=False) as writer:
                with profile_input_mutation(writer, self.user_id):
                    fit = writer.scalar(select(FitProfile).where(FitProfile.user_id == self.user_id))
                    fit.budget_max -= 1
                writer.commit()
            return rows

        with patch.object(feed_session, 'generate_feed', side_effect=rank):
            with self.assertRaises(HTTPException) as caught:
                self.feed()
        self.assertEqual(caught.exception.status_code, 409)
        self.assertIn('Обновите ленту', caught.exception.detail)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(calls))
        self.assert_delivery([])

    def test_changed_product_evidence_is_reranked_and_never_published_stale(self):
        original = feed_session.generate_feed
        calls, changed = [], []

        def rank(db, *args, **kwargs):
            rows = original(db, *args, **kwargs)
            calls.append([row.product.id for row in rows])
            if len(calls) == 1:
                changed.append(rows[0].product.id)
                with Session(self.engine) as writer:
                    writer.get(Product, changed[0]).price = Decimal('10000.99')
                    writer.commit()
            return rows

        with patch.object(feed_session, 'generate_feed', side_effect=rank):
            response = self.feed()
        self.assertEqual(len(calls), 2)
        self.assertTrue(response)
        self.assert_delivery(response, forbidden=changed)

    def test_new_source_rule_after_prepare_is_rechecked_before_publication(self):
        original = feed_session.generate_feed
        calls = []

        def rank(db, *args, **kwargs):
            rows = original(db, *args, **kwargs)
            calls.append([row.product.id for row in rows])
            if len(calls) == 1:
                with Session(self.engine) as writer, patch.dict(os.environ, {'ADMIN_TOKEN': 'SYNTHETIC_ALPHA_ADMIN'}):
                    admin_catalog_source_rule_create('merchant', SourceRuleCreate(rule_type='blocked_brand',
                        rule_value='One affordable brand'), writer, 'Bearer SYNTHETIC_ALPHA_ADMIN')
            return rows

        with patch.object(feed_session, 'generate_feed', side_effect=rank):
            response = self.feed()
        self.assertTrue(calls[0])
        self.assertEqual(len(calls), 2)
        self.assertEqual(response, [])
        self.assert_delivery(response)

    def test_waiting_feedback_reloads_preloaded_taste_and_state_without_lost_likes(self):
        sku = self.add_product('concurrent-liked-item', brand='Concurrent Brand', colors=['blue'])

        def like(db, *, commit=True):
            return record_product_event(db, db.get(User, self.user_id),
                EventRequest(product_id=sku, event_type='like', meta={'surface': 'feed'}), commit=commit)

        def state(db):
            return db.scalar(select(UserProductState).where(
                UserProductState.user_id == self.user_id, UserProductState.product_id == sku))

        def taste(db):
            return db.scalar(select(TasteProfile).where(TasteProfile.user_id == self.user_id))

        def brand_feature(db):
            return db.scalar(select(UserTasteFeature).where(UserTasteFeature.user_id == self.user_id,
                UserTasteFeature.feature_type == 'brand', UserTasteFeature.feature_value == 'concurrent brand'))

        with Session(self.engine, autoflush=False) as db:
            like(db)
            baseline_weights = dict(taste(db).brand_weights)
            baseline_strength = state(db).event_strength
            baseline_positive = brand_feature(db).positive_count
            baseline_revision = db.get(User, self.user_id).recommendation_revision

        loaded, first_applied, attempting, release, second_done = (threading.Event() for _ in range(5))
        second_pid = []

        def before_cursor_execute(connection, cursor, statement, parameters, context, executemany):
            if threading.current_thread().name.endswith('_1') and 'FROM users' in statement and 'FOR UPDATE' in statement:
                attempting.set()

        def first():
            with Session(self.engine, autoflush=False) as db:
                if not loaded.wait(4):
                    raise AssertionError('Second writer did not preload its stale personal rows')
                like(db, commit=False)
                first_applied.set()
                if not release.wait(4):
                    raise AssertionError('Missing deterministic first-writer commit')
                db.commit()

        def second():
            with Session(self.engine, autoflush=False) as db:
                second_pid.append(db.scalar(text('SELECT pg_backend_pid()')))
                cached_user, cached_taste, cached_state = db.get(User, self.user_id), taste(db), state(db)
                self.assertEqual(cached_taste.brand_weights, baseline_weights)
                self.assertEqual(cached_state.event_strength, baseline_strength)
                loaded.set()
                if not first_applied.wait(4):
                    raise AssertionError('First writer did not apply its uncommitted like')
                # Keep strong references: identity-map expiry at entry must
                # refresh these exact objects after the actual owner-lock wait.
                like(db)
                self.assertEqual(cached_user.recommendation_revision, baseline_revision + 2)
                self.assertEqual(cached_taste.brand_weights['concurrent brand'],
                    baseline_weights['concurrent brand'] + 4)
                self.assertEqual(cached_state.event_strength, baseline_strength + 4)
                second_done.set()

        event.listen(self.engine, 'before_cursor_execute', before_cursor_execute)
        try:
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix='feedback-race') as executor:
                # Explicit submission order makes the waiting writer's thread
                # name deterministic for the SQL-before-wait signal above.
                earlier = executor.submit(first)
                later = executor.submit(second)
                try:
                    self.assertTrue(attempting.wait(4))
                    self.assert_backend_waits_on_lock(second_pid[0], second_done)
                finally:
                    release.set()
                earlier.result(timeout=5)
                later.result(timeout=5)
        finally:
            event.remove(self.engine, 'before_cursor_execute', before_cursor_execute)
        with Session(self.engine) as db:
            self.assertEqual(taste(db).brand_weights['concurrent brand'], baseline_weights['concurrent brand'] + 4)
            self.assertEqual(state(db).event_strength, baseline_strength + 4)
            self.assertEqual(brand_feature(db).positive_count, baseline_positive + 2)
            self.assertEqual(db.scalar(select(func.count()).select_from(RecommendationEventV2).where(
                RecommendationEventV2.user_id == self.user_id, RecommendationEventV2.product_id == sku,
                RecommendationEventV2.event_type == 'like')), 3)

    def test_hidden_identity_change_after_prepare_cannot_publish_unchanged_alias(self):
        hidden_id = self.add_product('hidden-identity-item')
        self.feedback(hidden_id)
        original, forbidden, calls = feed_session._publish, [], []

        def publish(sessions, user_id, proposal, **options):
            calls.append(proposal)
            if len(calls) == 1:
                self.assertTrue(proposal.finalists)
                forbidden.append(proposal.finalists[0].product_id)
                with sessions() as db:
                    owner_revision = db.get(User, user_id).recommendation_revision
                    unchanged = db.get(Product, forbidden[0])
                    before = feed_session._product_evidence(unchanged)
                    db.get(Product, hidden_id).image_url = unchanged.image_url
                    db.commit()
                    self.assertEqual(db.get(User, user_id).recommendation_revision, owner_revision)
                    self.assertEqual(feed_session._product_evidence(db.get(Product, forbidden[0])), before)
            return original(sessions, user_id, proposal, **options)

        with patch.object(feed_session, '_publish', side_effect=publish):
            response = self.feed()
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(response), 5)
        self.assert_delivery(response, forbidden={hidden_id, *forbidden})

    def _catalogue_contention_leaves_feedback_free(self, model, *, locked_product_id=None):
        lock_attempted, owner_attempted = threading.Event(), threading.Event()
        original_owner = feed_session.owner_mutation

        def before_cursor_execute(connection, cursor, statement, parameters, context, executemany):
            # This signal occurs immediately before the actual PostgreSQL wait,
            # not merely before a worker is scheduled or an arbitrary sleep.
            if f'FROM {model.__tablename__}' in statement and 'FOR SHARE' in statement:
                lock_attempted.set()

        @contextmanager
        def owner(*args, **kwargs):
            owner_attempted.set()
            with original_owner(*args, **kwargs) as mutation:
                yield mutation

        event.listen(self.engine, 'before_cursor_execute', before_cursor_execute)
        try:
            with self.ranking_barrier() as (ready, release, calls, selected):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    delivery = executor.submit(self.feed)
                    try:
                        self.assertTrue(ready.wait(4))
                        with Session(self.engine) as importer:
                            key = 'merchant' if model is ProductSource else locked_product_id or selected[0]
                            importer.scalar(select(model.id).where(model.id == key).with_for_update())
                            with patch.object(feed_session, 'owner_mutation', side_effect=owner):
                                release.set()
                                self.assertTrue(lock_attempted.wait(2))
                                self.assertFalse(owner_attempted.is_set(), 'Importer wait already owns User lock')
                                # Another SKU avoids the importer's own Product FK
                                # lock while exercising this same owner's queue.
                                executor.submit(self.feedback, selected[1]).result(timeout=2)
                                self.assertFalse(owner_attempted.is_set())
                                importer.commit()
                                response = delivery.result(timeout=5)
                    finally:
                        release.set()
            self.assertEqual(len(calls), 2)
            self.assertTrue(response)
            self.assert_delivery(response, forbidden={selected[1]})
        finally:
            event.remove(self.engine, 'before_cursor_execute', before_cursor_execute)

    def test_source_import_wait_precedes_owner_lock_and_does_not_block_feedback(self):
        self._catalogue_contention_leaves_feedback_free(ProductSource)

    def test_product_import_wait_precedes_owner_lock_and_does_not_block_feedback(self):
        self._catalogue_contention_leaves_feedback_free(Product)

    def test_hidden_identity_import_wait_also_precedes_owner_lock(self):
        hidden_id = self.add_product('hidden-lock-item')
        self.feedback(hidden_id)
        self._catalogue_contention_leaves_feedback_free(Product, locked_product_id=hidden_id)

    def test_bootstrap_rule_writer_waits_for_shared_source_publication_lock(self):
        attempting, completed = threading.Event(), threading.Event()
        writer_pid = []

        def before_cursor_execute(connection, cursor, statement, parameters, context, executemany):
            if 'FROM product_sources' in statement and 'FOR UPDATE' in statement:
                attempting.set()

        def bootstrap():
            with Session(self.engine, autoflush=False) as db:
                writer_pid.append(db.scalar(text('SELECT pg_backend_pid()')))
                added = _ensure_clothing_rules(db, source_id='merchant')
                db.commit()
                completed.set()
                return added

        event.listen(self.engine, 'before_cursor_execute', before_cursor_execute)
        try:
            with ThreadPoolExecutor(max_workers=1) as executor, Session(self.engine) as publisher:
                publisher.scalar(select(ProductSource.id).where(ProductSource.id == 'merchant')
                    .with_for_update(read=True))
                writer = executor.submit(bootstrap)
                try:
                    self.assertTrue(attempting.wait(2))
                    self.assert_backend_waits_on_lock(writer_pid[0], completed)
                finally:
                    publisher.commit()
                self.assertGreater(writer.result(timeout=5), 0)
            with Session(self.engine) as db:
                count = db.scalar(select(func.count()).select_from(SourceRule).where(SourceRule.source_id == 'merchant'))
                self.assertGreater(count, 0)
                self.assertEqual(_ensure_clothing_rules(db, source_id='merchant'), 0)
                db.commit()
        finally:
            event.remove(self.engine, 'before_cursor_execute', before_cursor_execute)
