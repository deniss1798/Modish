"""Infrastructure health owns factory, SELECT and cleanup in isolated databases."""
from contextlib import contextmanager
import os
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.main import API_VERSION, health, liveness, readiness
from app.middleware import RateLimitMiddleware
from app.models import User
from app.services.health_probe import database_probe_succeeds


class HealthReadinessTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False},
            poolclass=StaticPool)
        self.addCleanup(self.engine.dispose)
        self.sessions = sessionmaker(bind=self.engine, autoflush=False)
        with self.engine.begin() as connection:
            connection.execute(text('CREATE TABLE probe_state (value INTEGER NOT NULL)'))
            connection.execute(text('INSERT INTO probe_state (value) VALUES (7)'))
        self.app = FastAPI()
        self.app.get('/health')(health)
        self.app.get('/health/live')(liveness)
        self.app.get('/health/ready')(readiness)
        self.app.get('/limited')(lambda: {'status': 'limited'})
        self.app.get('/health/private')(lambda: {'status': 'ordinary'})
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.sessions_seen = []

        def database():
            with self.sessions() as db:
                self.sessions_seen.append(db)
                yield db

        self.app.dependency_overrides[get_db] = database
        self.good = {'status': 'ok', 'database': 'ok', 'version': API_VERSION}
        self.bad = {'status': 'degraded', 'database': 'error', 'version': API_VERSION}

    def assert_degraded(self):
        for path, status in (('/health/ready', 503), ('/health', 200)):
            response = self.client.get(path)
            self.assertEqual(response.status_code, status, response.text)
            self.assertEqual(response.json(), self.bad)
            self.assertNotIn('SYNTHETIC_PRIVATE_DIAGNOSTIC', response.text)

    def test_real_database_uses_only_select_then_rolls_back_and_closes_the_override(self):
        statements, rollbacks, checkouts = [], [], []

        def statement(_connection, _cursor, sql, *_unused):
            statements.append(sql.strip().upper())

        def rollback(_connection):
            rollbacks.append(True)

        def checkout(_connection, _record, _proxy):
            checkouts.append(1)

        def checkin(_connection, _record):
            checkouts.append(-1)

        for name, handler in (('before_cursor_execute', statement), ('rollback', rollback),
                ('checkout', checkout), ('checkin', checkin)):
            event.listen(self.engine, name, handler)
            self.addCleanup(event.remove, self.engine, name, handler)
        with patch('app.api.deps.SessionLocal',
                side_effect=AssertionError('An override must never fall back to the application database')):
            for path in ('/health/ready', '/health'):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json(), self.good)
        self.assertEqual(statements, ['SELECT 1', 'SELECT 1'])
        self.assertEqual(len(rollbacks), 2)
        self.assertEqual(sum(checkouts), 0)
        self.assertTrue(all(not db.in_transaction() for db in self.sessions_seen))
        self.assertTrue(all(not db.identity_map for db in self.sessions_seen))
        with self.engine.connect() as connection:
            self.assertEqual(connection.scalar(text('SELECT value FROM probe_state')), 7)

    def test_default_get_db_factory_is_inside_the_guard_and_can_use_an_isolated_session_factory(self):
        self.app.dependency_overrides.clear()
        with patch('app.api.deps.SessionLocal', self.sessions):
            response = self.client.get('/health/ready')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), self.good)

    def test_factory_failure_before_yield_is_degraded_not_an_unhandled_server_error(self):
        self.app.dependency_overrides.clear()
        with patch('app.api.deps.SessionLocal',
                side_effect=RuntimeError('SYNTHETIC_PRIVATE_DIAGNOSTIC factory credentials')):
            self.assert_degraded()

    def test_broken_override_never_falls_back_to_the_application_database(self):
        def broken_override():
            raise RuntimeError('SYNTHETIC_PRIVATE_DIAGNOSTIC override')

        self.app.dependency_overrides[get_db] = broken_override
        with patch('app.api.deps.SessionLocal',
                side_effect=AssertionError('No application database fallback')) as fallback:
            self.assert_degraded()
        fallback.assert_not_called()

    def test_lazy_connection_acquisition_failure_still_closes_the_created_real_session(self):
        def unavailable_connection():
            raise RuntimeError('SYNTHETIC_PRIVATE_DIAGNOSTIC connection refused')

        broken = create_engine('sqlite://', creator=unavailable_connection)
        self.addCleanup(broken.dispose)
        closed = []

        class TrackingSession(Session):
            def close(session):
                closed.append(True)
                super().close()

        self.app.dependency_overrides[get_db] = sessionmaker(bind=broken, class_=TrackingSession)
        self.assert_degraded()
        self.assertEqual(len(closed), 2)

    def test_actual_execute_failure_rolls_back_and_closes_without_driver_details(self):
        rollbacks, closed = [], []

        def broken_execute(_connection, _cursor, _sql, *_unused):
            raise RuntimeError('SYNTHETIC_PRIVATE_DIAGNOSTIC SQL SELECT password')

        event.listen(self.engine, 'before_cursor_execute', broken_execute)
        self.addCleanup(event.remove, self.engine, 'before_cursor_execute', broken_execute)
        event.listen(self.engine, 'rollback', lambda _connection: rollbacks.append(True))

        class TrackingSession(Session):
            def close(session):
                closed.append(True)
                super().close()

        self.app.dependency_overrides[get_db] = sessionmaker(bind=self.engine, class_=TrackingSession)
        self.assert_degraded()
        self.assertEqual(len(rollbacks), 2)
        self.assertEqual(len(closed), 2)

    def test_close_failure_cannot_publish_a_successful_readiness_after_a_good_select(self):
        closed = []

        class BrokenCloseSession(Session):
            def close(session):
                super().close()
                closed.append(True)
                raise RuntimeError('SYNTHETIC_PRIVATE_DIAGNOSTIC close')

        self.app.dependency_overrides[get_db] = sessionmaker(bind=self.engine, class_=BrokenCloseSession)
        self.assert_degraded()
        self.assertEqual(len(closed), 2)

    def test_override_finalization_failure_is_also_degraded(self):
        @contextmanager
        def broken_finalization():
            with self.sessions() as db:
                yield db
            raise RuntimeError('SYNTHETIC_PRIVATE_DIAGNOSTIC provider exit')

        self.app.dependency_overrides[get_db] = broken_finalization
        self.assert_degraded()

    def test_liveness_never_opens_a_database_even_with_a_broken_override_and_default_factory(self):
        def forbidden_override():
            raise AssertionError('Liveness must not resolve get_db')

        self.app.dependency_overrides[get_db] = forbidden_override
        with patch('app.api.deps.SessionLocal', side_effect=AssertionError('Liveness must not create a session')):
            response = self.client.get('/health/live')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {'status': 'ok', 'version': API_VERSION})
        self.assertEqual(self.sessions_seen, [])

    def test_unsupported_provider_fails_closed_without_awaiting_or_falling_back(self):
        async def asynchronous_override():
            raise AssertionError('Unsupported async providers must not be entered')

        for provider in (asynchronous_override, lambda: None, lambda: object()):
            with self.subTest(provider=provider):
                self.app.dependency_overrides[get_db] = provider
                self.assert_degraded()

    def test_probes_are_independent_of_alpha_credentials_and_exhausted_general_auth_quotas(self):
        app = FastAPI()
        app.get('/health')(health)
        app.get('/health/live')(liveness)
        app.get('/health/ready')(readiness)
        app.get('/limited')(lambda: {'ok': True})
        app.get('/health/private')(lambda: {'ok': True})
        app.post('/auth/login')(lambda: {'ok': True})
        app.add_middleware(RateLimitMiddleware, limit=1, max_clients=1, auth_login_limit=1)
        app.dependency_overrides[get_db] = self.app.dependency_overrides[get_db]
        with TestClient(app) as client, patch.dict(os.environ,
                {'CLOSED_ALPHA_ENABLED': 'true', 'CLOSED_ALPHA_ALLOWED_EMAILS': ''}):
            self.assertEqual(client.get('/limited').status_code, 200)
            self.assertEqual(client.get('/limited').status_code, 429)
            self.assertEqual(client.post('/auth/login').status_code, 200)
            self.assertEqual(client.post('/auth/login').status_code, 429)
            for _ in range(3):
                for path in ('/health', '/health/live', '/health/ready'):
                    self.assertEqual(client.get(path,
                        headers={'Authorization': 'Bearer SYNTHETIC_INVALID_TOKEN'}).status_code, 200)
            self.assertEqual(client.get('/health/private').status_code, 429)
            self.assertEqual(client.post('/auth/login').status_code, 429)

    def test_all_probe_responses_are_not_cacheable(self):
        for path in ('/health', '/health/live', '/health/ready'):
            response = self.client.get(path)
            self.assertEqual(response.headers['Cache-Control'], 'no-store')

    def test_probe_does_not_flush_or_commit_a_session_with_pending_caller_data(self):
        # Health owns a new session in normal use. A caller-supplied session
        # must not make SELECT 1 implicitly write unrelated pending rows.
        class NoWritesSession(Session):
            def flush(self, *args, **kwargs):
                raise AssertionError('The infrastructure probe cannot flush application data')

            def commit(self):
                raise AssertionError('The infrastructure probe cannot commit application data')

        sessions = []

        def pending_session():
            db = NoWritesSession(bind=self.engine)
            db.add(User(id='never-persisted-health-row', email='probe@example.test', password_hash='unused'))
            sessions.append(db)
            return db

        self.assertTrue(database_probe_succeeds(pending_session))
        self.assertFalse(sessions[0].new)
        self.assertFalse(sessions[0].in_transaction())


if __name__ == '__main__':
    unittest.main()
