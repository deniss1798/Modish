"""Cold Style Swipe HTTP races against a real disposable PostgreSQL."""
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

from app.api import onboarding
from app.api.deps import get_db
from app.models import ActivationSession, Product, ProductSource, RecommendationEventV2, User
from app.services import activation_deck_session as decks, activation_service as activation
from isolated_postgres import create_isolated_postgres
import test_closed_alpha_first_session as fixtures


@unittest.skipUnless(os.getenv('MODISH_ISOLATED_PG_RUNTIME'), 'Opt-in disposable PostgreSQL fixture')
class ActivationDeckSessionPostgresTests(unittest.TestCase):
    def setUp(self):
        isolated = create_isolated_postgres(self, namespace='deck')
        self.engine = isolated.engine
        fixture = fixtures.ClosedAlphaFirstSessionTests()
        with Session(self.engine, autoflush=False) as db:
            fixture.db, fixture.size, fixture.maximum = db, 'M', Decimal('10000')
            fixture.user = User(id='cold-deck-owner', email='deck-pg@example.test', password_hash='unused')
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
            db.commit()
            self.user_id = fixture.user.id
            self.initial_revision = fixture.user.recommendation_revision
            self.assertIsNone(fixture.session.completed_at)
        app, self.auth_reads = FastAPI(), []
        app.include_router(onboarding.router)

        def database(request: Request):
            with Session(self.engine, autoflush=False) as db:
                if request.method == 'GET':
                    self.auth_reads.append(db)
                yield db

        def authenticated_owner(credentials, db):
            return db.get(User, self.user_id)

        app.dependency_overrides[get_db] = database
        auth = patch.object(onboarding, 'user_from_token', side_effect=authenticated_owner)
        auth.start()
        self.addCleanup(auth.stop)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def get_deck(self):
        return self.client.get('/onboarding/style-swipe')

    @contextmanager
    def ranking_barrier(self):
        ready, release, calls = threading.Event(), threading.Event(), []
        original = decks.swipe_deck

        def rank(db, *args, **kwargs):
            self.assertTrue(kwargs['read_only'])
            self.assertEqual(db.scalar(text('SHOW transaction_read_only')), 'on')
            self.assertEqual(db.scalar(text('SHOW transaction_isolation')), 'repeatable read')
            response = original(db, *args, **kwargs)
            calls.append(tuple(row['id'] for row in response['products']))
            if len(calls) == 1:
                self.assertTrue(response['products'])
                ready.set()
                if not release.wait(4):
                    raise AssertionError('Missing deterministic deck ranking release')
            return response

        with patch.object(decks, 'swipe_deck', side_effect=rank):
            try:
                yield ready, release, calls
            finally:
                release.set()

    def test_like_http_commits_while_get_ranks_and_removes_old_card_with_correct_count(self):
        initial = self.get_deck()
        self.assertEqual(initial.status_code, 200, initial.text)
        self.assertEqual(len(initial.json()['products']), 20)
        liked = initial.json()['products'][0]['id']
        with self.ranking_barrier() as (ready, release, calls):
            with ThreadPoolExecutor(max_workers=2) as executor:
                delivery = executor.submit(self.get_deck)
                try:
                    self.assertTrue(ready.wait(4))
                    self.assertFalse(self.auth_reads[-1].in_transaction())
                    self.assertEqual(len(self.auth_reads[-1].identity_map), 0)
                    feedback = executor.submit(self.client.post, '/onboarding/style-swipe', json={
                        'product_id': liked, 'reaction': 'like',
                    }).result(timeout=2)
                    self.assertEqual(feedback.status_code, 200, feedback.text)
                    self.assertEqual(feedback.json()['rated'], 1)
                finally:
                    release.set()
                response = delivery.result(timeout=5)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(calls), 2)
        self.assertEqual(response.json()['rated'], 1)
        self.assertEqual(len(response.json()['products']), 19)
        self.assertNotIn(liked, {row['id'] for row in response.json()['products']})
        with Session(self.engine) as db:
            session = db.get(ActivationSession, self.user_id)
            self.assertEqual(session.swipes_json, {liked: 'like'})
            self.assertEqual(db.scalar(select(func.count()).select_from(RecommendationEventV2)), 1)

    def test_two_gets_keep_committed_allocation_and_retry_without_training_taste(self):
        with self.ranking_barrier() as (ready, release, calls):
            with ThreadPoolExecutor(max_workers=2) as executor:
                earlier = executor.submit(self.get_deck)
                try:
                    self.assertTrue(ready.wait(4))
                    winner = executor.submit(self.get_deck).result(timeout=2)
                    self.assertEqual(winner.status_code, 200, winner.text)
                    self.assertEqual(len(winner.json()['products']), 20)
                finally:
                    release.set()
                retried = earlier.result(timeout=5)
        self.assertEqual(retried.status_code, 200, retried.text)
        self.assertEqual(len(calls), 3, 'The first proposal must detect another GET allocation and rerank once')
        expected = [row['id'] for row in winner.json()['products']]
        self.assertEqual([row['id'] for row in retried.json()['products']], expected)
        with Session(self.engine) as db:
            self.assertEqual(db.get(ActivationSession, self.user_id).swipe_product_ids, expected)
            self.assertEqual(db.get(User, self.user_id).recommendation_revision, self.initial_revision)
            self.assertEqual(db.scalar(select(func.count()).select_from(RecommendationEventV2)), 0)

    def test_repeated_product_conflict_returns_409_without_partial_allocation(self):
        original, calls = decks._publish, []

        def publish(sessions, user_id, proposal):
            calls.append(proposal)
            with sessions() as db:
                db.get(Product, proposal.finalists[0].product_id).price += Decimal('0.01')
                db.commit()
            return original(sessions, user_id, proposal)

        with patch.object(decks, '_publish', side_effect=publish):
            response = self.get_deck()
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(len(calls), 2)
        with Session(self.engine) as db:
            self.assertEqual(db.get(ActivationSession, self.user_id).swipe_product_ids, [])
            self.assertEqual(db.get(User, self.user_id).recommendation_revision, self.initial_revision)
            self.assertEqual(db.scalar(select(func.count()).select_from(RecommendationEventV2)), 0)
