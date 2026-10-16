"""Real photo quota admission races with a held, entirely fake provider."""
import asyncio
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import os
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from app import business
from app.api import onboarding, profile, recommendations
from app.api.deps import create_access_token, get_db
from app.models import PhotoAnalysisReservation, Product, RecommendationEventV2, StyleProfile, User, UserLimits
from app.services import photo_analysis_jobs as jobs
from isolated_postgres import create_isolated_postgres
import test_photo_reservations as fixtures


@unittest.skipUnless(os.getenv('MODISH_ISOLATED_PG_RUNTIME'), 'Opt-in disposable PostgreSQL fixture')
class PhotoReservationsPostgresTests(unittest.TestCase):
    def setUp(self):
        isolated = create_isolated_postgres(self, namespace='photo')
        self.engine = isolated.engine
        self.sessions = sessionmaker(bind=self.engine, autoflush=False)
        self.job_sessions = []

        def job_session():
            db = self.sessions()
            self.job_sessions.append(db)
            return db

        factory = patch.object(jobs, 'SessionLocal', side_effect=job_session)
        factory.start()
        self.addCleanup(factory.stop)
        app = FastAPI()
        for api in (onboarding, profile, recommendations):
            app.include_router(api.router)

        def database():
            with self.sessions() as db:
                yield db

        app.dependency_overrides[get_db] = database
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def make_user(self):
        self.user_id, self.product_id = str(uuid4()), str(uuid4())
        with self.sessions() as db:
            db.add(User(id=self.user_id, email=f'{self.user_id}@example.test',
                password_hash='unused', subscription_status='active'))
            db.flush()
            db.add(StyleProfile(id=str(uuid4()), user_id=self.user_id,
                profile_json={'analysis': deepcopy(fixtures.ANALYSIS)}))
            db.add(Product(id=self.product_id, external_id=self.product_id, source='baon',
                title='Женская рубашка хлопковая', brand='Synthetic photo brand', category='рубашки', price=1200,
                currency='RUB', gender_target='womenswear', available_sizes=['M'],
                feed_raw_json={'size_system': 'LETTER'}, colors=['black'], style_tags=['casual'],
                image_url=f'https://baon.ru/images/{self.product_id}.jpg',
                product_url=f'https://baon.ru/products/{self.product_id}',
                is_active=1, is_available=1, availability_status='in_stock',
                last_seen_in_feed_at=datetime.now(timezone.utc)))
            limits = business.get_or_refresh_limits(db, self.user_id)
            limits.photo_analysis_used = business.PLUS_MONTHLY_PHOTO_ANALYSES - 1
            self.used_before = limits.photo_analysis_used
            db.commit()
        self.token = create_access_token(self.user_id)
        self.job_sessions.clear()

    def post_photo(self, endpoint, key):
        return self.client.post(endpoint, headers={
            'Authorization': f'Bearer {self.token}', 'Idempotency-Key': key,
        }, files={'photo': ('photo.jpg', b'SYNTHETIC_PHOTO_BYTES', 'image/jpeg')})

    def post_feedback(self):
        return self.client.post('/recommendations/events', headers={'Authorization': f'Bearer {self.token}'},
            json={'product_id': self.product_id, 'event_type': 'like', 'meta': {'surface': 'feed'}})

    def patch_profile(self):
        return self.client.patch('/style-profile/target', headers={'Authorization': f'Bearer {self.token}'},
            json={'style_target': 'womenswear'})

    def quota_state(self):
        with self.sessions() as db:
            limits = db.scalar(select(UserLimits).where(UserLimits.user_id == self.user_id))
            rows = db.scalars(select(PhotoAnalysisReservation).where(
                PhotoAnalysisReservation.user_id == self.user_id)).all()
            return limits.photo_analysis_used, [
                (row.request_id, row.kind, row.status, deepcopy(row.response_json)) for row in rows]

    def assert_photo_sessions_released(self):
        self.assertTrue(self.job_sessions)
        self.assertTrue(all(not db.in_transaction() for db in self.job_sessions))
        self.assertTrue(all(not db.identity_map for db in self.job_sessions))
        self.assertEqual(self.engine.pool.checkedout(), 0,
            'A held external provider must not keep a database connection')

    @contextmanager
    def held_provider(self, target, result, *, simultaneous_admission=False):
        entered, release, calls = threading.Event(), threading.Event(), []
        rendezvous, original, counter, lock = threading.Barrier(2), jobs._owner, [], threading.Lock()

        async def provider(**kwargs):
            self.assertEqual(kwargs, {'content_type': 'image/jpeg', 'raw': b'SYNTHETIC_PHOTO_BYTES'})
            calls.append(1)
            entered.set()
            if not await asyncio.to_thread(release.wait, 8):
                raise AssertionError('Missing deterministic fake photo provider release')
            return deepcopy(result)

        def admit(db, credentials):
            with lock:
                counter.append(1)
                position = len(counter)
            if position <= 2:
                rendezvous.wait(timeout=4)
            return original(db, credentials)

        with ExitStack() as stack:
            stack.enter_context(patch(target, new=provider))
            if simultaneous_admission:
                stack.enter_context(patch.object(jobs, '_owner', side_effect=admit))
            try:
                yield entered, release, calls
            finally:
                release.set()

    def test_both_endpoints_one_slot_two_parallel_keys_admit_exactly_one_provider_and_debit(self):
        for endpoint, target, result in fixtures.ENDPOINTS:
            with self.subTest(endpoint=endpoint):
                self.make_user()
                with self.held_provider(target, result, simultaneous_admission=True) as (entered, release, calls):
                    with ThreadPoolExecutor(max_workers=2) as executor:
                        requests = [executor.submit(self.post_photo, endpoint, f'parallel-photo-{index}') for index in range(2)]
                        try:
                            self.assertTrue(entered.wait(4))
                            completed, pending = wait(requests, timeout=2, return_when=FIRST_COMPLETED)
                            self.assertEqual(len(completed), 1)
                            self.assertEqual(len(pending), 1)
                            denied = next(iter(completed)).result()
                            self.assertEqual(denied.status_code, 403, denied.text)
                            self.assertEqual(len(calls), 1)
                            self.assert_photo_sessions_released()
                            used, rows = self.quota_state()
                            self.assertEqual(used, self.used_before + 1)
                            self.assertEqual(len(rows), 1)
                            self.assertEqual(rows[0][2], 'pending')
                        finally:
                            release.set()
                        responses = [request.result(timeout=5) for request in requests]
                self.assertEqual(sorted(response.status_code for response in responses), [200, 403])
                self.assertEqual(len(calls), 1)
                used, rows = self.quota_state()
                self.assertEqual(used, self.used_before + 1)
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0][2], 'completed')
                self.assert_photo_sessions_released()

    def test_both_endpoints_parallel_same_key_pending_then_replay_do_not_call_or_charge_again(self):
        for endpoint, target, result in fixtures.ENDPOINTS:
            with self.subTest(endpoint=endpoint):
                self.make_user()
                with self.held_provider(target, result, simultaneous_admission=True) as (entered, release, calls):
                    with ThreadPoolExecutor(max_workers=2) as executor:
                        requests = [executor.submit(self.post_photo, endpoint, 'same-photo-key') for _ in range(2)]
                        try:
                            self.assertTrue(entered.wait(4))
                            completed, pending = wait(requests, timeout=2, return_when=FIRST_COMPLETED)
                            self.assertEqual(len(completed), 1)
                            self.assertEqual(len(pending), 1)
                            duplicate = next(iter(completed)).result()
                            self.assertEqual(duplicate.status_code, 409, duplicate.text)
                            self.assertEqual(duplicate.headers.get('Retry-After'), '2')
                            self.assertEqual(len(calls), 1)
                            self.assert_photo_sessions_released()
                        finally:
                            release.set()
                        responses = [request.result(timeout=5) for request in requests]
                    success = next(response for response in responses if response.status_code == 200)
                    before = self.quota_state()
                    with self.sessions() as db:
                        before_revision = db.get(User, self.user_id).recommendation_revision
                    replay = self.post_photo(endpoint, 'same-photo-key')
                    self.assertEqual(replay.status_code, 200, replay.text)
                    self.assertEqual(replay.json(), success.json())
                    self.assertEqual(len(calls), 1)
                    self.assertEqual(self.quota_state(), before)
                    with self.sessions() as db:
                        self.assertEqual(db.get(User, self.user_id).recommendation_revision, before_revision)
                used, rows = self.quota_state()
                self.assertEqual(used, self.used_before + 1)
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0][2], 'completed')

    def test_both_held_providers_leave_pool_and_same_owner_feedback_profile_writes_usable(self):
        for endpoint, target, result in fixtures.ENDPOINTS:
            with self.subTest(endpoint=endpoint):
                self.make_user()
                with self.held_provider(target, result) as (entered, release, calls):
                    with ThreadPoolExecutor(max_workers=2) as executor:
                        request = executor.submit(self.post_photo, endpoint, 'held-photo-provider')
                        try:
                            self.assertTrue(entered.wait(4))
                            self.assert_photo_sessions_released()
                            with self.sessions() as db:
                                self.assertEqual(db.scalar(text('SELECT 1')), 1)
                            feedback = executor.submit(self.post_feedback).result(timeout=2)
                            self.assertEqual(feedback.status_code, 200, feedback.text)
                            changed = executor.submit(self.patch_profile).result(timeout=2)
                            self.assertEqual(changed.status_code, 200, changed.text)
                            self.assertEqual(changed.json()['style_target'], 'womenswear')
                            self.assertFalse(request.done(), 'The provider barrier must still hold the photo response')
                            self.assert_photo_sessions_released()
                            self.assertEqual(self.quota_state()[1][0][2], 'pending')
                        finally:
                            release.set()
                        response = request.result(timeout=5)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(len(calls), 1)
                self.assertEqual(self.quota_state()[1][0][2], 'completed')
                with self.sessions() as db:
                    current = db.scalar(select(StyleProfile).where(StyleProfile.user_id == self.user_id))
                    self.assertEqual(current.style_target, 'womenswear', 'The result cannot overwrite an unrelated profile edit')
                    events = list(db.scalars(select(RecommendationEventV2).where(RecommendationEventV2.user_id == self.user_id)))
                    self.assertEqual(len(events), 1)
                    self.assertEqual(events[0].event_type, 'like')
                self.assert_photo_sessions_released()
