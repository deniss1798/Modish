"""Actual PostgreSQL JSON/timestamptz evidence for first20 reason attribution."""
from datetime import datetime, timedelta, timezone
import os
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, select, text
from sqlalchemy.orm import sessionmaker

from app.api.deps import create_access_token, get_db
from app.api.recommendations import router
from app.models import ActivationExposure, Product, RecommendationEventV2
from app.services.activation_service import activation_metrics
from isolated_postgres import create_isolated_postgres
import test_first_session_dislike_reasons as fixtures


@unittest.skipUnless(os.getenv('MODISH_ISOLATED_PG_RUNTIME'), 'Opt-in disposable PostgreSQL fixture')
class FirstSessionDislikeReasonsPostgresTests(unittest.TestCase):
    # Reuse independent test-data builders without importing a discoverable
    # TestCase class or substituting any real metric query/scoring behaviour.
    user = fixtures.FirstSessionDislikeReasonsTests.user
    expose = fixtures.FirstSessionDislikeReasonsTests.expose
    feedback = fixtures.FirstSessionDislikeReasonsTests.feedback
    metrics = fixtures.FirstSessionDislikeReasonsTests.metrics

    def setUp(self):
        isolated = create_isolated_postgres(self, namespace='first20_reasons')
        self.engine = isolated.engine

        def set_timezone(connection, _record):
            previous = connection.autocommit
            connection.autocommit = True
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET TIME ZONE 'Europe/Moscow'")
            finally:
                connection.autocommit = previous

        event.listen(self.engine, 'connect', set_timezone)
        # Schema creation used the initial pooled connection. Reconnect this
        # fixture engine so every test transaction gets the intended timezone.
        self.engine.dispose()
        self.sessions = sessionmaker(bind=self.engine, autoflush=False)
        self.db = self.sessions()
        self.addCleanup(self.db.close)
        self.assertEqual(self.db.scalar(text('SHOW timezone')), 'Europe/Moscow')
        self.now = datetime.now(timezone.utc)
        self.products = [f'first20-pg-{index}' for index in range(4)]
        self.db.add_all([Product(id=pid, external_id=pid, source='baon', title='Женская рубашка',
            category='рубашки', brand='Fixture', gender_target='womenswear', price=1500,
            available_sizes=['M'], colors=['black'], style_tags=['casual'],
            feed_raw_json={'size_system': 'LETTER'}, image_url=f'https://baon.ru/{pid}.jpg',
            product_url=f'https://baon.ru/p/{pid}', is_active=1, is_available=1,
            availability_status='in_stock', last_seen_in_feed_at=self.now) for pid in self.products])
        self.owner = self.user('pg-owner')
        self.db.commit()

    def test_current_reason_dedup_owner_and_unknown_metadata_use_real_postgres_json(self):
        self.expose(0)
        foreign = self.user('pg-foreign')
        self.feedback(0, owner=foreign, reason='too_expensive')
        self.expose(1)
        self.feedback(1, reason='dont_like_brand', at=self.now - timedelta(minutes=20))
        self.feedback(1, reason='dont_like_color')
        self.feedback(1, reason='dont_like_color')
        self.feedback(1, reason='not_my_style', surface='style_swipe',
            at=self.now - timedelta(minutes=1))
        self.expose(2)
        self.feedback(2, metadata=['historical malformed metadata'])
        self.feedback(2, reason='dont_like_design', at=self.now + timedelta(seconds=1))
        self.db.commit()
        result = self.metrics()['dislike_reasons']
        self.assertEqual(result['disliked_cards'], 3)
        self.assertEqual(result['without_reason_cards'], 2)
        self.assertEqual(result['top_reasons'], [
            {'reason': 'dont_like_color', 'count': 1, 'share_percent': 33.33}])

    def test_first_http_action_creates_exposure_at_the_exact_accepted_event_clock(self):
        app = FastAPI()
        app.include_router(router)

        def database():
            with self.sessions() as db:
                yield db

        app.dependency_overrides[get_db] = database
        with TestClient(app) as client:
            response = client.post('/recommendations/events',
                headers={'Authorization': 'Bearer ' + create_access_token(self.owner.id)},
                json={'product_id': self.products[0], 'event_type': 'dislike',
                    'meta': {'surface': 'feed', 'reason': 'dont_like_fit'}})
        self.assertEqual(response.status_code, 200, response.text)
        self.db.expire_all()
        exposure = self.db.scalar(select(ActivationExposure))
        refusal = self.db.scalar(select(RecommendationEventV2))
        self.assertEqual(exposure.shown_at, refusal.created_at)
        self.assertEqual(exposure.shown_at.utcoffset(), timedelta(hours=3))
        self.assertEqual(activation_metrics(self.db)['dislike_reasons']['top_reasons'], [
            {'reason': 'dont_like_fit', 'count': 1, 'share_percent': 100.0}])
