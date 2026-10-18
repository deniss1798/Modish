"""Optional closed-alpha policy gates actual authentication and catalogue HTTP.

Only synthetic local accounts are used. No participant list or external service
is read, and policy normalization never changes account identity semantics.
"""
from copy import deepcopy
from datetime import datetime, timezone
import os
import unittest
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import auth
from app.api.deps import create_access_token, get_db, hash_password
from app.api.products import router as products_router
from app.api.profile import router as profile_router
from app.db import Base
from app.models import AuthSession, Product, User
from app.services.alpha_access import (ALPHA_ACCESS_MESSAGE, MAX_ALPHA_EMAILS,
    alpha_access_required, require_alpha_access)
from app.services.auth_sessions import issue_session


PASSWORD = 'SYNTHETIC_ALPHA_PASSWORD_123'
WRONG_PASSWORD = 'SYNTHETIC_WRONG_PASSWORD_123'


class AlphaAccessPolicyTests(unittest.TestCase):
    def denied(self, email='allowed@example.com'):
        with self.assertRaises(HTTPException) as error:
            require_alpha_access(email)
        self.assertEqual(error.exception.status_code, 403)
        self.assertEqual(error.exception.detail, ALPHA_ACCESS_MESSAGE)

    def test_default_off_and_explicit_off_preserve_access_even_with_an_invalid_unused_list(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(alpha_access_required())
            require_alpha_access('historical-user@example.com')
        for value in ('false', '0', ' FALSE '):
            with self.subTest(value=value), patch.dict(os.environ,
                    {'CLOSED_ALPHA_ENABLED': value, 'CLOSED_ALPHA_ALLOWED_EMAILS': 'invalid'}):
                self.assertFalse(alpha_access_required())
                require_alpha_access('historical-user@example.com')

    def test_blank_or_misspelled_enabled_flag_fails_closed(self):
        for value in ('', 'yes', 'TRUEE', '2', 'unexpected-setting'):
            with self.subTest(value=value), patch.dict(os.environ,
                    {'CLOSED_ALPHA_ENABLED': value, 'CLOSED_ALPHA_ALLOWED_EMAILS': 'allowed@example.com'}):
                self.assertTrue(alpha_access_required())
                self.denied()

    def test_enabled_empty_malformed_wildcard_or_oversized_lists_fail_closed_as_a_whole(self):
        malformed = ('', ' ', ',', 'allowed@example.com,',
            'allowed@example.com,invalid', 'Display Name <allowed@example.com>',
            '*@example.com', 'example.com', 'allowed@example.com\n',
            ','.join(['allowed@example.com'] * (MAX_ALPHA_EMAILS + 1)), 'x' * 13000)
        for value in malformed:
            with self.subTest(length=len(value)), patch.dict(os.environ,
                    {'CLOSED_ALPHA_ENABLED': 'true', 'CLOSED_ALPHA_ALLOWED_EMAILS': value}):
                self.denied()

    def test_bounded_exact_list_accepts_casefold_but_not_different_plus_or_dot_aliases(self):
        emails = [f'user{index}@example.com' for index in range(MAX_ALPHA_EMAILS - 1)]
        emails.append('Allowed.Case+Tag@Example.COM')
        with patch.dict(os.environ, {'CLOSED_ALPHA_ENABLED': '1',
                'CLOSED_ALPHA_ALLOWED_EMAILS': ', '.join(emails)}):
            require_alpha_access('allowed.case+tag@example.com')
            require_alpha_access('user0@example.com')
            for value in ('allowedcase+tag@example.com', 'allowed.case@example.com',
                    'allowed.case+other@example.com', 'stranger@example.com'):
                self.denied(value)


class AlphaAccessHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password_hash = hash_password(PASSWORD)

    def setUp(self):
        self.policy = patch.dict(os.environ, {'CLOSED_ALPHA_ENABLED': 'true',
            'CLOSED_ALPHA_ALLOWED_EMAILS': 'allowed.case@example.com'})
        self.policy.start()
        self.addCleanup(self.policy.stop)
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False},
            poolclass=StaticPool)
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, autoflush=False)
        self.db = self.sessions()
        self.addCleanup(self.db.close)
        self.allowed = User(id='alpha-allowed', email='Allowed.Case@example.com',
            password_hash=self.password_hash)
        self.blocked = User(id='alpha-blocked', email='blocked@example.com',
            password_hash=self.password_hash)
        self.product = Product(id='alpha-product', external_id='alpha-product', source='baon',
            title='Женская рубашка хлопковая', brand='Fixture', category='рубашки', price=1500,
            gender_target='womenswear', available_sizes=['M'], feed_raw_json={'size_system': 'LETTER'},
            colors=['black'], style_tags=['casual'], image_url='https://baon.ru/alpha-product.jpg',
            product_url='https://baon.ru/p/alpha-product', is_active=1, is_available=1,
            availability_status='in_stock', last_seen_in_feed_at=datetime.now(timezone.utc))
        self.db.add_all([self.allowed, self.blocked, self.product])
        self.db.commit()
        app = FastAPI()
        for router in (auth.router, profile_router, products_router):
            app.include_router(router)

        def database():
            with self.sessions() as db:
                yield db

        app.dependency_overrides[get_db] = database
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def state(self):
        # Compare values as well as counts: a denied login must not reset an
        # existing quota, alter a profile, issue a session or touch timestamps.
        with self.sessions() as db:
            return {table.name: deepcopy([dict(row) for row in db.execute(
                table.select().order_by(*table.primary_key.columns)).mappings()])
                for table in Base.metadata.sorted_tables}

    def headers(self, user=None):
        return {'Authorization': 'Bearer ' + create_access_token((user or self.allowed).id)}

    def login(self, *, email='Allowed.Case@example.com', password=PASSWORD):
        return self.client.post('/auth/login', json={'email': email, 'password': password})

    def denied(self, response):
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json(), {'detail': ALPHA_ACCESS_MESSAGE})

    def test_denied_registration_precedes_existing_lookup_hash_and_all_mutations(self):
        before = self.state()
        with patch.object(auth, 'hash_password', side_effect=AssertionError('Denied registration cannot hash')):
            for email in ('new-unlisted@example.com', self.blocked.email):
                self.denied(self.client.post('/auth/register', json={'email': email, 'password': PASSWORD}))
        self.assertEqual(self.state(), before)

    def test_allowed_registration_preserves_email_identity_and_creates_a_real_session(self):
        with patch.dict(os.environ, {'CLOSED_ALPHA_ALLOWED_EMAILS': 'new.case@example.com'}):
            response = self.client.post('/auth/register',
                json={'email': 'New.Case@example.com', 'password': PASSWORD})
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            self.assertTrue(body['refresh_token'])
            self.assertEqual(body['user']['email'], 'New.Case@example.com')
            with self.sessions() as db:
                self.assertIsNotNone(db.scalar(select(User).where(User.email == 'New.Case@example.com')))
                self.assertIsNone(db.scalar(select(User).where(User.email == 'new.case@example.com')))
                self.assertEqual(db.scalar(select(func.count()).select_from(AuthSession)), 1)
            self.assertEqual(self.client.get('/users/me',
                headers={'Authorization': 'Bearer ' + body['access_token']}).status_code, 200)

    def test_default_off_preserves_registration_and_an_existing_nonlisted_login(self):
        with patch.dict(os.environ, {'CLOSED_ALPHA_ENABLED': 'false'}):
            registered = self.client.post('/auth/register',
                json={'email': 'open-mode@example.com', 'password': PASSWORD})
            self.assertEqual(registered.status_code, 200, registered.text)
            logged_in = self.login(email=self.blocked.email)
            self.assertEqual(logged_in.status_code, 200, logged_in.text)

    def test_denied_login_checks_real_credentials_but_does_not_create_profiles_quotas_or_sessions(self):
        before = self.state()
        self.denied(self.login(email=self.blocked.email))
        self.assertEqual(self.state(), before)
        for email in (self.allowed.email, self.blocked.email, 'unknown@example.com'):
            bad = self.login(email=email, password=WRONG_PASSWORD)
            self.assertEqual(bad.status_code, 401, bad.text)
            self.assertEqual(bad.json(), {'detail': 'Неверный email или пароль'})
        self.assertEqual(self.state(), before)

    def test_allowed_login_does_not_silently_change_case_sensitive_account_lookup(self):
        wrong_identity = self.login(email='allowed.case@example.com')
        self.assertEqual(wrong_identity.status_code, 401)
        allowed = self.login()
        self.assertEqual(allowed.status_code, 200, allowed.text)
        self.assertEqual(allowed.json()['user']['email'], self.allowed.email)

    def test_policy_removal_blocks_existing_access_refresh_and_session_tokens_without_mutation(self):
        response = self.login()
        self.assertEqual(response.status_code, 200, response.text)
        tokens = response.json()
        headers = {'Authorization': 'Bearer ' + tokens['access_token']}
        self.assertEqual(self.client.get('/users/me', headers=headers).status_code, 200)
        before = self.state()
        with patch.dict(os.environ, {'CLOSED_ALPHA_ALLOWED_EMAILS': 'someone-else@example.com'}):
            self.denied(self.client.get('/users/me', headers=headers))
            self.denied(self.client.post('/auth/session', headers=headers))
            self.denied(self.client.post('/auth/refresh', json={'refresh_token': tokens['refresh_token']}))
            self.denied(self.client.post('/metrics/events', headers=headers,
                json={'name': 'alpha_probe', 'meta': {}}))
            for path in ('/products', '/products/brands', '/products/alpha-product', '/feed'):
                self.denied(self.client.get(path, headers=headers))
        self.assertEqual(self.state(), before)

    def test_preexisting_unlisted_user_is_not_grandfathered_and_session_response_is_also_guarded(self):
        old_refresh = issue_session(self.db, self.blocked.id)
        self.db.commit()
        before = self.state()
        headers = self.headers(self.blocked)
        self.denied(self.client.get('/users/me', headers=headers))
        self.denied(self.client.post('/auth/session', headers=headers))
        self.denied(self.client.post('/auth/refresh', json={'refresh_token': old_refresh}))
        with self.sessions() as db, self.assertRaises(HTTPException) as error:
            auth.session_response(db, db.get(User, self.blocked.id))
        self.assertEqual(error.exception.detail, ALPHA_ACCESS_MESSAGE)
        self.assertEqual(self.state(), before)

    def test_logout_remains_available_after_policy_removal_and_refresh_stays_revoked(self):
        response = self.login()
        self.assertEqual(response.status_code, 200, response.text)
        refresh = response.json()['refresh_token']
        with patch.dict(os.environ, {'CLOSED_ALPHA_ALLOWED_EMAILS': ''}):
            self.denied(self.client.post('/auth/refresh', json={'refresh_token': refresh}))
            logout = self.client.post('/auth/logout', json={'refresh_token': refresh})
            self.assertEqual(logout.status_code, 200, logout.text)
            self.assertEqual(logout.json(), {'ok': True})
        with self.sessions() as db:
            self.assertIsNotNone(db.scalar(select(AuthSession)).revoked_at)
        self.assertEqual(self.client.post('/auth/refresh',
            json={'refresh_token': refresh}).status_code, 401)

    def test_anonymous_and_invalid_tokens_cannot_read_catalogue_when_alpha_is_enabled(self):
        before = self.state()
        for path in ('/products', '/products/brands', '/products/alpha-product'):
            self.assertEqual(self.client.get(path).status_code, 401)
            self.assertEqual(self.client.get(path,
                headers={'Authorization': 'Bearer SYNTHETIC_INVALID_TOKEN'}).status_code, 401)
            self.assertEqual(self.client.get(path, headers=self.headers()).status_code, 200)
        self.assertEqual(self.state(), before)
        with patch.dict(os.environ, {'CLOSED_ALPHA_ENABLED': 'false'}):
            for path in ('/products', '/products/brands', '/products/alpha-product'):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_malformed_enabled_configuration_denies_existing_tokens_without_exposing_values(self):
        before = self.state()
        for changes in ({'CLOSED_ALPHA_ALLOWED_EMAILS': ''},
                {'CLOSED_ALPHA_ALLOWED_EMAILS': 'allowed.case@example.com,invalid-private-value'},
                {'CLOSED_ALPHA_ENABLED': 'unrecognized-private-setting'}):
            with patch.dict(os.environ, changes):
                self.denied(self.client.get('/users/me', headers=self.headers()))
        self.assertEqual(self.state(), before)


if __name__ == '__main__':
    unittest.main()
