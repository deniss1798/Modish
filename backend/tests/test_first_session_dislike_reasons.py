"""First20 reasons use actual owned exposure/feedback, never client telemetry."""
from datetime import datetime, timedelta, timezone
import json
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.analytics import router as analytics_router
from app.api.deps import create_access_token, get_db
from app.api.profile import router as profile_router
from app.api.recommendations import router as reactions_router
from app.db import Base
from app.models import (ActivationExposure, ActivationSession, MetricEvent, Product,
    RecommendationEventV2, User)
from app.services.activation_service import activation_metrics


class FirstSessionDislikeReasonsTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False},
            poolclass=StaticPool)
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, autoflush=False)
        self.db = self.sessions()
        self.addCleanup(self.db.close)
        self.now = datetime.now(timezone.utc)
        self.products = [f'first20-product-{i:02}' for i in range(25)]
        self.db.add_all([Product(id=pid, external_id=pid, source='baon', title='Рубашка',
            category='рубашки', brand='Fixture', gender_target='womenswear', price=1500,
            available_sizes=['M'], colors=['black'], style_tags=['casual'],
            feed_raw_json={'size_system': 'LETTER'}, image_url=f'https://baon.ru/{pid}.jpg',
            product_url=f'https://baon.ru/p/{pid}', is_active=1, is_available=1,
            availability_status='in_stock', last_seen_in_feed_at=self.now)
            for pid in self.products])
        self.owner = self.user('owner')
        self.db.commit()
        app = FastAPI()
        app.include_router(analytics_router)
        app.include_router(reactions_router)
        app.include_router(profile_router)

        def database():
            with self.sessions() as db:
                yield db

        app.dependency_overrides[get_db] = database
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.headers = {'Authorization': 'Bearer ' + create_access_token(self.owner.id)}

    def user(self, name, *, completed_at=None, completed=True):
        user = User(id=f'first20-{name}', email=f'{name}@example.test', password_hash='unused')
        self.db.add(user)
        self.db.flush()
        self.db.add(ActivationSession(user_id=user.id,
            completed_at=(completed_at or self.now - timedelta(hours=2)) if completed else None,
            issued_feed_ids=self.products))
        self.db.flush()
        return user

    def expose(self, index, *, owner=None, reaction='dislike', position=None, shown_at=None):
        row = ActivationExposure(id=str(uuid4()), user_id=(owner or self.owner).id,
            product_id=self.products[index], position=position or index + 1, reaction=reaction,
            shown_at=shown_at or self.now - timedelta(hours=1))
        self.db.add(row)
        self.db.flush()
        return row

    def feedback(self, index, *, owner=None, action='dislike', reason='dont_like_color',
                 at=None, surface='feed', metadata=None):
        values = {'surface': surface}
        if reason is not None:
            values['reason'] = reason
        if metadata is not None:
            values = metadata
        self.db.add(RecommendationEventV2(id=str(uuid4()), user_id=(owner or self.owner).id,
            product_id=self.products[index], event_type=action, event_weight=-3,
            meta_json=values, created_at=at or self.now - timedelta(minutes=10)))
        self.db.flush()

    def metrics(self):
        with patch('app.services.activation_service.datetime', wraps=datetime) as clock:
            clock.now.return_value = self.now
            metrics = activation_metrics(self.db, days=7)
        breakdown = metrics['dislike_reasons']
        self.assertEqual(breakdown['disliked_cards'], metrics['dislike'])
        self.assertEqual(sum(row['count'] for row in breakdown['top_reasons'])
            + breakdown['without_reason_cards'], metrics['dislike'])
        return metrics

    def test_latest_reason_counts_once_and_skips_likes_views_do_not_supply_dislike_reasons(self):
        self.expose(0)
        self.feedback(0, reason='dont_like_color', at=self.now - timedelta(minutes=20))
        self.feedback(0, reason='dont_like_brand')
        self.feedback(0, reason='dont_like_brand')
        self.feedback(0, action='view', reason='too_expensive', at=self.now - timedelta(minutes=1))
        self.expose(1, reaction='skip')
        self.feedback(1, action='skip', reason='not_my_style')
        self.expose(2, reaction='like')
        self.feedback(2, reason='not_my_style')
        self.feedback(2, action='like', at=self.now - timedelta(minutes=1))
        self.expose(3)
        self.feedback(3, reason=None)
        self.feedback(3, action='view', reason='dont_like_color')
        result = self.metrics()['dislike_reasons']
        self.assertEqual(result['disliked_cards'], 2)
        self.assertEqual(result['without_reason_cards'], 1)
        self.assertEqual(result['top_reasons'], [
            {'reason': 'dont_like_brand', 'count': 1, 'share_percent': 50.0}])

    def test_owner_and_first_twenty_membership_are_required_even_for_the_same_product(self):
        self.expose(0)
        other = self.user('other')
        self.feedback(0, owner=other, reason='too_expensive')
        self.expose(1, owner=other)
        self.feedback(1, owner=other, reason='dont_like_color')
        self.expose(20, position=21)
        self.feedback(20, reason='not_my_style')
        self.feedback(24, reason='dont_like_category')  # Delivered/known, never seen.
        result = self.metrics()
        self.assertEqual(result['unique_first20_views'], 2)
        self.assertEqual(result['dislike_reasons']['without_reason_cards'], 1)
        self.assertEqual(result['dislike_reasons']['top_reasons'], [
            {'reason': 'dont_like_color', 'count': 1, 'share_percent': 50.0}])

    def test_latest_reasonless_dislike_does_not_reuse_an_older_explicit_reason(self):
        self.expose(0)
        self.feedback(0, reason='dont_like_color', at=self.now - timedelta(minutes=20))
        self.feedback(0, reason=None, at=self.now - timedelta(minutes=10))
        self.feedback(0, action='view', reason='too_expensive', at=self.now - timedelta(minutes=1))
        result = self.metrics()['dislike_reasons']
        self.assertEqual(result['disliked_cards'], 1)
        self.assertEqual(result['without_reason_cards'], 1)
        self.assertEqual(result['with_reason_cards'], 0)
        self.assertEqual(result['top_reasons'], [])

    def test_pre_view_out_of_window_future_and_style_swipe_events_cannot_supply_a_reason(self):
        self.expose(0)
        self.feedback(0, at=self.now - timedelta(hours=1, seconds=1), reason='dont_like_brand')
        self.feedback(0, at=self.now - timedelta(days=8), reason='too_expensive')
        self.feedback(0, at=self.now + timedelta(seconds=1), reason='dont_like_category')
        self.feedback(0, surface='style_swipe', reason='not_my_style')
        self.feedback(0, surface='catalogue_discovery', reason='dont_like_design')
        result = self.metrics()['dislike_reasons']
        self.assertEqual(result['without_reason_cards'], 1)
        self.assertEqual(result['top_reasons'], [])
        # A post-view permanent detail reaction remains attributable after the
        # original item has disappeared from the bounded issued-ID list.
        self.db.get(ActivationSession, self.owner.id).issued_feed_ids = []
        self.feedback(0, surface='product_detail', reason='dont_like_fit')
        self.assertEqual(self.metrics()['dislike_reasons']['top_reasons'][0]['reason'], 'dont_like_fit')

    def test_completed_cohort_and_exposure_times_are_bounded(self):
        self.expose(0)
        self.feedback(0)
        for name, completed_at, completed in (
                ('old', self.now - timedelta(days=8), True),
                ('future', self.now + timedelta(seconds=1), True),
                ('unfinished', None, False)):
            owner = self.user(name, completed_at=completed_at, completed=completed)
            self.expose(0, owner=owner)
            self.feedback(0, owner=owner, reason='too_expensive')
        self.expose(1, shown_at=self.now + timedelta(seconds=1))
        self.feedback(1, reason='not_my_style')
        self.expose(2, shown_at=self.now - timedelta(hours=3))  # Before completing onboarding.
        self.feedback(2, reason='dont_like_category')
        result = self.metrics()
        self.assertEqual(result['completed_onboarding_users'], 1)
        self.assertEqual(result['unique_first20_views'], 1)
        self.assertEqual(result['dislike_reasons']['top_reasons'], [
            {'reason': 'dont_like_color', 'count': 1, 'share_percent': 100.0}])

    def test_unknown_or_malformed_historical_reasons_never_become_metric_dimensions(self):
        for index, reason in enumerate((None, 'test@example.invalid private free text',
                {'email': 'test@example.invalid'}, ['dont_like_color'], 'x' * 10000)):
            self.expose(index)
            self.feedback(index, reason=reason)
        self.expose(5)
        self.feedback(5, reason='  DONT_LIKE_COLOR  ')
        self.expose(6)  # Historical current refusal with no rich event at all.
        result = self.metrics()['dislike_reasons']
        self.assertEqual(result['without_reason_cards'], 6)
        self.assertEqual(result['with_reason_cards'], 1)
        self.assertEqual(len(result['top_reasons']), 1)
        self.assertEqual(result['top_reasons'][0]['reason'], 'dont_like_color')
        self.assertNotIn('example.invalid', json.dumps(result))
        self.assertNotIn('private free text', json.dumps(result))

    def test_client_telemetry_does_not_supply_a_reason_and_empty_cohort_is_explicit(self):
        self.expose(0)
        self.db.commit()
        telemetry = self.client.post('/metrics/events', headers=self.headers,
            json={'name': 'reason_dislike',
                'meta': {'product_id': self.products[0], 'reason': 'too_expensive'}})
        self.assertEqual(telemetry.status_code, 200, telemetry.text)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(MetricEvent)), 1)
        self.assertEqual(self.metrics()['dislike_reasons']['without_reason_cards'], 1)
        self.db.get(ActivationSession, self.owner.id).completed_at = None
        self.db.flush()
        self.assertEqual(self.metrics()['dislike_reasons']['top_reasons'], [])
        self.assertEqual(self.metrics()['dislike_reasons']['disliked_cards'], 0)

    def test_first_action_http_dislike_uses_the_same_accepted_clock_as_new_exposure(self):
        self.db.commit()
        response = self.client.post('/recommendations/events', headers=self.headers,
            json={'product_id': self.products[0], 'event_type': 'dislike',
                'meta': {'surface': 'feed', 'reason': 'too_expensive'}})
        self.assertEqual(response.status_code, 200, response.text)
        self.db.expire_all()
        exposure = self.db.scalar(select(ActivationExposure))
        refusal = self.db.scalar(select(RecommendationEventV2).where(
            RecommendationEventV2.event_type == 'dislike'))
        self.assertEqual(exposure.shown_at, refusal.created_at)
        # The admin route retains its access restriction and includes the new
        # additive summary; no client telemetry payload is trusted for it.
        self.assertEqual(self.client.get('/analytics/first-session').status_code, 403)
        with patch.dict('os.environ', {'ADMIN_CATALOG_TOKEN': 'SYNTHETIC_ANALYTICS_ADMIN'}):
            result = self.client.get('/analytics/first-session',
                headers={'Authorization': 'Bearer SYNTHETIC_ANALYTICS_ADMIN'})
        self.assertEqual(result.status_code, 200, result.text)
        body = result.json()
        self.assertEqual(body['dislike'], 1)
        self.assertEqual(body['dislike_reasons']['top_reasons'], [
            {'reason': 'too_expensive', 'count': 1, 'share_percent': 100.0}])

    def test_repeated_http_views_and_twenty_first_card_cannot_inflate_the_reason_counts(self):
        self.db.commit()
        for index in range(21):
            for action in ('view', 'view', 'dislike'):
                response = self.client.post('/recommendations/events', headers=self.headers,
                    json={'product_id': self.products[index], 'event_type': action,
                        'meta': {'surface': 'feed', 'reason': 'not_my_style'}})
                self.assertEqual(response.status_code, 200, response.text)
        self.db.expire_all()
        result = activation_metrics(self.db)
        self.assertEqual(result['unique_first20_views'], 20)
        self.assertEqual(result['dislike'], 20)
        self.assertEqual(result['dislike_reasons']['top_reasons'], [
            {'reason': 'not_my_style', 'count': 20, 'share_percent': 100.0}])
        self.assertEqual(self.db.scalar(select(func.count()).select_from(RecommendationEventV2)), 63)

    def test_breakdown_is_read_only_and_top_order_is_stable(self):
        for index, reason in enumerate(('not_my_style', 'dont_like_color', 'not_my_style')):
            self.expose(index)
            self.feedback(index, reason=reason)
        self.db.commit()
        statements = []

        def record(_connection, _cursor, sql, *_unused):
            statements.append(sql.strip().split()[0].upper())

        event.listen(self.engine, 'before_cursor_execute', record)
        try:
            result = self.metrics()['dislike_reasons']
        finally:
            event.remove(self.engine, 'before_cursor_execute', record)
        self.assertEqual(set(statements), {'SELECT'})
        self.assertEqual(result['top_reasons'], [
            {'reason': 'not_my_style', 'count': 2, 'share_percent': 66.67},
            {'reason': 'dont_like_color', 'count': 1, 'share_percent': 33.33}])

    def test_reported_days_match_the_effective_one_to_ninety_day_window(self):
        self.expose(0)
        self.feedback(0)
        for name, age in (('sixty-days', 60), ('hundred-days', 100)):
            owner = self.user(name, completed_at=self.now - timedelta(days=age))
            self.expose(0, owner=owner)
            self.feedback(0, owner=owner, reason='too_expensive')
        with patch('app.services.activation_service.datetime', wraps=datetime) as clock:
            clock.now.return_value = self.now
            minimum = activation_metrics(self.db, days=0)
            maximum = activation_metrics(self.db, days=365)
        self.assertEqual(minimum['days'], 1)
        self.assertEqual(minimum['completed_onboarding_users'], 1)
        self.assertEqual(minimum['dislike_reasons']['disliked_cards'], 1)
        self.assertEqual(maximum['days'], 90)
        self.assertEqual(maximum['completed_onboarding_users'], 2)
        self.assertEqual(maximum['dislike_reasons']['disliked_cards'], 2)


if __name__ == '__main__':
    unittest.main()
