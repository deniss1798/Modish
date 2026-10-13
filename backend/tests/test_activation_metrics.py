"""First-session analytics count actual exposures and retain late responses."""
import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import ActivationExposure, ActivationSession, Product, User
from app.services.activation_service import activation_metrics, record_exposure_event


class ActivationMetricsTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, autoflush=False)()
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        self.now = datetime.now(timezone.utc)
        self.products = []
        for _ in range(21):
            pid = str(uuid4())
            self.products.append(Product(id=pid, external_id=pid, source='test',
                title='Рубашка', brand='Test', category='рубашки', price=2000,
                image_url='https://shop.example/image.jpg', product_url='https://shop.example/product'))
        self.db.add_all(self.products)
        self.db.flush()

    def user(self, *, completed=True, completed_at=None):
        uid = str(uuid4())
        user = User(id=uid, email=f'{uid}@example.test', password_hash='unused')
        self.db.add(user)
        self.db.flush()
        session = ActivationSession(user_id=uid,
            completed_at=(completed_at or self.now) if completed else None,
            issued_feed_ids=[p.id for p in self.products])
        self.db.add(session)
        self.db.flush()
        return user, session

    def expose(self, user, count):
        for product in self.products[:count]:
            record_exposure_event(self.db, user, product.id, 'view', surface='feed')

    def test_empty_and_unstarted_cohorts_have_no_feed_denominator(self):
        empty = activation_metrics(self.db)
        self.assertEqual(empty['completed_onboarding_users'], 0)
        self.assertEqual(empty['started_feed_users'], 0)
        self.assertIsNone(empty['reached_card10_rate'])
        self.assertIsNone(empty['onboarding_to_card10_rate'])
        user, _ = self.user()
        # Issued cards and detail views outside the feed do not start it.
        record_exposure_event(self.db, user, self.products[0].id, 'open_product')
        metrics = activation_metrics(self.db)
        self.assertEqual(metrics['completed_onboarding_users'], 1)
        self.assertEqual(metrics['started_feed_users'], 0)
        self.assertEqual(metrics['unique_first20_views'], 0)
        self.assertIsNone(metrics['like_save_rate'])
        self.assertIsNone(metrics['reached_card10_rate'])
        self.assertEqual(metrics['onboarding_to_card10_rate'], 0)

    def test_card10_denominator_is_started_users_in_the_completed_cohort(self):
        reached, _ = self.user()
        stopped, _ = self.user()
        self.user()  # Completed onboarding, never opened the feed.
        old, _ = self.user(completed_at=self.now - timedelta(days=10))
        unfinished, _ = self.user(completed=False)
        self.expose(reached, 10)
        self.expose(stopped, 1)
        self.expose(old, 20)
        self.expose(unfinished, 10)
        metrics = activation_metrics(self.db, days=7)
        self.assertEqual(metrics['completed_onboarding_users'], 3)
        self.assertEqual(metrics['started_feed_users'], 2)
        self.assertEqual(metrics['unique_first20_views'], 11)
        self.assertEqual(metrics['reached_card10_users'], 1)
        self.assertEqual(metrics['reached_card10_rate'], 50)
        self.assertEqual(metrics['onboarding_to_card10_rate'], 33.33)

    def test_existing_exposure_accepts_late_events_after_issued_ids_eviction(self):
        user, session = self.user()
        first = self.products[0].id
        self.expose(user, 1)
        session.issued_feed_ids = [p.id for p in self.products[1:]]
        self.db.flush()
        for event in ('like', 'save', 'open_product', 'affiliate_click', 'affiliate_click'):
            record_exposure_event(self.db, user, first, event, surface='product_detail')
        metrics = activation_metrics(self.db)
        self.assertEqual(metrics['unique_first20_views'], 1)
        self.assertEqual(metrics['like_or_save'], 1)
        self.assertEqual(metrics['open_product'], 1)
        self.assertEqual(metrics['affiliate_click'], 1)
        self.assertEqual(metrics['affiliate_click_rate'], 100)
        # Style Swipe remains outside the first-feed measurement even if a
        # caller submits a product which already has an exposure.
        record_exposure_event(self.db, user, first, 'dislike', surface='style_swipe')
        self.assertEqual(activation_metrics(self.db)['dislike'], 0)

    def test_only_explicit_issued_feed_interactions_create_new_exposures(self):
        user, session = self.user()
        first, second = self.products[:2]
        session.issued_feed_ids = [second.id]
        self.db.flush()
        record_exposure_event(self.db, user, first.id, 'view', surface='feed')
        for surface in (None, 'product_detail', 'style_swipe'):
            record_exposure_event(self.db, user, second.id, 'affiliate_click', surface=surface)
        self.assertEqual(activation_metrics(self.db)['unique_first20_views'], 0)
        record_exposure_event(self.db, user, second.id, 'skip', surface='feed')
        metrics = activation_metrics(self.db)
        self.assertEqual(metrics['unique_first20_views'], 1)
        self.assertEqual(metrics['like_or_save'], 0)
        self.assertEqual(metrics['dislike'], 0)

    def test_repeated_views_reactions_and_cap_preserve_unique_first_twenty(self):
        user, _ = self.user()
        self.expose(user, 21)
        self.expose(user, 21)
        for event in ('like', 'like', 'save', 'save'):
            record_exposure_event(self.db, user, self.products[0].id, event, surface='feed')
        record_exposure_event(self.db, user, self.products[1].id, 'dislike', surface='feed')
        record_exposure_event(self.db, user, self.products[2].id, 'skip', surface='feed')
        rows = list(self.db.scalars(select(ActivationExposure).where(ActivationExposure.user_id == user.id)))
        self.assertEqual(sorted(row.position for row in rows), list(range(1, 21)))
        metrics = activation_metrics(self.db)
        self.assertEqual(metrics['unique_first20_views'], 20)
        self.assertEqual(metrics['like_or_save'], 1)
        self.assertEqual(metrics['dislike'], 1)
        self.assertEqual(metrics['like_save_rate'], 5)
        self.assertEqual(metrics['dislike_rate'], 5)


if __name__ == '__main__':
    unittest.main()
