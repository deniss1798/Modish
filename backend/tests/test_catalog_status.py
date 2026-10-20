"""HTTP stock diagnostics distinguish global gaps without guessing personal fit."""
from datetime import datetime, timedelta, timezone
import os
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import create_access_token, get_db
from app.api.products import router
from app.db import Base
from app.models import Product, User
from app.services.catalog_status import catalogue_status


class CatalogStatusTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False},
                                    poolclass=StaticPool)
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, autoflush=False)
        self.now = datetime.now(timezone.utc)
        self.sequence = 0
        policy = patch.dict(os.environ, {'CLOSED_ALPHA_ENABLED': 'false'})
        policy.start()
        self.addCleanup(policy.stop)
        app = FastAPI()
        app.include_router(router)

        def database():
            with self.sessions() as db:
                yield db

        app.dependency_overrides[get_db] = database
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def product(self, **changes):
        self.sequence += 1
        pid = f'status-{self.sequence}'
        values = dict(id=pid, external_id=pid, source='baon', title='Женская рубашка',
            brand='Fixture', category='рубашки', gender_target='womenswear',
            price=1000, currency='RUB', available_sizes=['L'],
            feed_raw_json={'size_system': 'LETTER'}, colors=['black'], style_tags=['casual'],
            image_url=f'https://baon.ru/images/{pid}.jpg',
            product_url=f'https://baon.ru/products/{pid}',
            is_active=1, is_available=1, is_deleted_from_feed=0,
            availability_status='in_stock', last_seen_in_feed_at=self.now,
            last_checked_at=self.now, updated_at=self.now)
        values.update(changes)
        with self.sessions() as db:
            db.add(Product(**values))
            db.commit()
        return pid

    def status(self, expected):
        response = self.client.get('/catalog/status')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        body = response.json()
        self.assertEqual(set(body), {'status', 'scope', 'personalized', 'checked_at', 'message'})
        self.assertEqual(body['status'], expected)
        self.assertEqual(body['scope'], 'global')
        self.assertIs(body['personalized'], False)
        self.assertIsNotNone(datetime.fromisoformat(body['checked_at']).tzinfo)
        return body

    def test_empty_database_and_nonavailable_deleted_inactive_or_demo_offers_are_empty(self):
        self.status('empty')
        for changes in (
            {'availability_status': 'out_of_stock'}, {'availability_status': 'unknown'},
            {'is_available': 0}, {'is_active': 0}, {'is_deleted_from_feed': 1},
            {'source': 'demo'},
        ):
            self.product(**changes)
        self.status('empty')

    def test_expired_missing_and_future_observations_require_refresh_not_size_changes(self):
        pids = [self.product(last_seen_in_feed_at=observed) for observed in (
            self.now - timedelta(hours=73), None, self.now + timedelta(hours=1),
        )]
        before = self.snapshot()
        self.status('refresh_required')
        self.assertEqual(self.snapshot(), before)
        # Neither ordinary viewing nor the diagnostics renew the source clock.
        self.assertEqual(self.client.get('/products/brands').json(), [])
        self.assertEqual(self.client.get('/products?limit=3').json(), [])
        self.assertEqual(len(pids), len(before))

    def test_fresh_offer_wins_over_any_number_of_stale_offers(self):
        for _ in range(60):
            self.product(last_seen_in_feed_at=self.now - timedelta(days=8))
        self.product(availability_status='available')
        self.status('ready')
        self.assertEqual(self.client.get('/products/brands').json(), [
            {'brand': 'Fixture', 'count': 1}])

    def test_same_exact_freshness_boundaries_and_timezone_as_product_read_gates(self):
        pid = self.product(last_seen_in_feed_at=self.now - timedelta(hours=72))
        with self.sessions() as db:
            self.assertEqual(catalogue_status(db, checked_at=self.now)['status'], 'ready')
            self.assertEqual(catalogue_status(db, checked_at=self.now + timedelta(microseconds=1))
                             ['status'], 'refresh_required')
            row = db.get(Product, pid)
            row.last_seen_in_feed_at = self.now + timedelta(minutes=5)
            db.commit()
            moscow = self.now.astimezone(timezone(timedelta(hours=3)))
            self.assertEqual(catalogue_status(db, checked_at=moscow)['status'], 'ready')
            self.assertEqual(catalogue_status(db, checked_at=self.now - timedelta(microseconds=1))
                             ['status'], 'refresh_required')

    def snapshot(self):
        with self.sessions() as db:
            return [dict(row) for row in db.execute(select(Product.__table__).order_by(Product.id))
                    .mappings()]

    def test_probe_is_one_select_without_row_materialization_or_pending_flush(self):
        self.product()
        statements = []

        def statement(_connection, _cursor, sql, *_args):
            statements.append(sql)

        event.listen(self.engine, 'before_cursor_execute', statement)
        self.addCleanup(event.remove, self.engine, 'before_cursor_execute', statement)
        with self.sessions() as db:
            db.autoflush = True
            row = db.get(Product, 'status-1')
            row.updated_at = self.now + timedelta(days=1)
            statements.clear()
            body = catalogue_status(db, checked_at=self.now)
            self.assertEqual(body['status'], 'ready')
            self.assertEqual(len(statements), 1)
            self.assertTrue(statements[0].lstrip().upper().startswith('SELECT'))
            self.assertEqual(statements[0].upper().count('EXISTS'), 2)
            self.assertIn(row, db.dirty)
            db.rollback()
        with self.sessions() as db:
            self.assertEqual(db.get(Product, 'status-1').updated_at,
                             self.now.replace(tzinfo=None))

    def test_ready_is_not_a_claim_of_personal_size_adult_fashion_or_budget_admission(self):
        self.product(title='Детская игрушка', category='игрушки', gender_target='unknown',
                     available_sizes=[], price=999999)
        body = self.status('ready')
        self.assertFalse(body['personalized'])
        self.assertEqual(self.client.get('/products').json(), [])

    def test_alpha_policy_matches_public_product_routes_without_leaking_catalogue_state(self):
        self.product()
        with self.sessions() as db:
            db.add_all([
                User(id='allowed-owner', email='allowed@example.com', password_hash='unused'),
                User(id='blocked-owner', email='blocked@example.com', password_hash='unused'),
            ])
            db.commit()
        with patch.dict(os.environ, {'CLOSED_ALPHA_ENABLED': 'true',
                                     'CLOSED_ALPHA_ALLOWED_EMAILS': 'allowed@example.com'}):
            for route in ('/catalog/status', '/products/brands'):
                self.assertEqual(self.client.get(route).status_code, 401)
                self.assertEqual(self.client.get(route, headers={
                    'Authorization': 'Bearer invalid'}).status_code, 401)
                blocked = self.client.get(route, headers={
                    'Authorization': 'Bearer ' + create_access_token('blocked-owner')})
                self.assertEqual(blocked.status_code, 403)
                self.assertNotIn('checked_at', blocked.json())
                allowed = self.client.get(route, headers={
                    'Authorization': 'Bearer ' + create_access_token('allowed-owner')})
                self.assertEqual(allowed.status_code, 200)
