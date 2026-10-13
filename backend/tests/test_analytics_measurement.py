"""Separate delivery/request telemetry from actual first-session actions."""
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

from fastapi import Request
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.api.affiliate import affiliate_click
from app.api.products import _feed_scored_items
from app.api.recommendations import EventRequest, record_product_event
from app.db import Base
from app.models import ActivationSession, AffiliateClick, Product, ProductImpression, User
from app.services.activation_service import activation_metrics, record_exposure_event
from app.services.product_analytics_service import analytics_summary, log_feed_impressions
from app.services.recommendation_engine import ScoredProduct


class AnalyticsMeasurementTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, autoflush=False)()
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        self.user = User(id=str(uuid4()), email='measurement@example.test', password_hash='unused')
        pid = str(uuid4())
        self.product = Product(id=pid, external_id=pid, source='baon', title='Женская рубашка',
            brand='BAON', category='рубашки', price=2000, gender_target='womenswear',
            available_sizes=['M'], image_url='https://shop.example/shirt.jpg',
            product_url='https://shop.example/shirt', last_seen_in_feed_at=datetime.now(timezone.utc),
            is_active=1, is_available=1, availability_status='in_stock')
        self.db.add_all([self.user, self.product])
        self.db.flush()
        self.db.add(ActivationSession(user_id=self.user.id, completed_at=datetime.now(timezone.utc),
            issued_feed_ids=[pid]))
        self.db.flush()

    def request_url(self):
        request = Request({'type': 'http', 'client': ('127.0.0.1', 12345)})
        with patch('app.api.affiliate.user_from_token', return_value=self.user):
            return affiliate_click(self.product.id, request, self.db, credentials=None, user_agent='test')

    def test_url_request_keeps_raw_click_but_only_launch_event_updates_first_session(self):
        record_exposure_event(self.db, self.user, self.product.id, 'view', surface='feed')
        for _ in range(2):
            response = self.request_url()
            self.assertEqual(response['url'], self.product.product_url)
            self.assertTrue(response['click_id'])
        self.assertEqual(self.db.scalar(select(func.count()).select_from(AffiliateClick)), 2)
        self.assertEqual(activation_metrics(self.db)['affiliate_click'], 0)
        # Browser failure produces no confirmation. Success uses the existing
        # recommendation event route; retries must not count the SKU twice.
        for _ in range(2):
            record_product_event(self.db, self.user, EventRequest(
                product_id=self.product.id, event_type='affiliate_click'))
        metrics = activation_metrics(self.db)
        self.assertEqual(metrics['unique_first20_views'], 1)
        self.assertEqual(metrics['affiliate_click'], 1)
        self.assertEqual(metrics['affiliate_click_rate'], 100)

    def test_served_algorithm_version_matches_persisted_delivery_metadata(self):
        scored = ScoredProduct(product=self.product, final_score=0.9, breakdown={'fit_score': 1},
            reason='В вашем стиле', reasons=['В вашем стиле'], candidate_sources=['style_category_pool'],
            algorithm_version='activation_rules_v1', ranking_algorithm='first_session_feed')
        with patch('app.services.activation_service.require_warm_feed'), \
                patch('app.api.products.generate_feed', return_value=[scored]):
            response = _feed_scored_items(self.db, self.user, limit=20)
        delivery = self.db.scalar(select(ProductImpression))
        self.assertEqual(delivery.algorithm_version, response[0]['algorithm_version'])
        self.assertEqual(delivery.algorithm_version, 'activation_rules_v1')
        self.assertEqual(delivery.ranking_meta_json['algorithm_version'], 'activation_rules_v1')
        self.assertEqual(delivery.ranking_meta_json['ranking_algorithm'], 'first_session_feed')
        self.assertEqual(delivery.rank_position, 1)
        self.assertEqual(activation_metrics(self.db)['unique_first20_views'], 0)

    def test_legacy_summary_retains_raw_counts_and_labels_its_denominator(self):
        for _ in range(2):
            log_feed_impressions(self.db, user_id=self.user.id,
                product_ids=[self.product.id, self.product.id])
        self.db.flush()
        for _ in range(3):
            self.request_url()
        summary = analytics_summary(self.db)
        self.assertEqual(summary['impressions'], 4)
        self.assertEqual(summary['clicks'], 3)
        self.assertEqual(summary['ctr_percent'], 75)
        self.assertIn('prefetch and repeats', summary['basis'])
        self.assertIn('/analytics/first-session', summary['basis'])
        self.assertEqual(activation_metrics(self.db)['unique_first20_views'], 0)


if __name__ == '__main__':
    unittest.main()
