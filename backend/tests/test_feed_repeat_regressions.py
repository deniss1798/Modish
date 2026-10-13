from datetime import datetime, timedelta, timezone
from unittest import TestCase
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.db import Base
from app.models import Product, User, RecommendationEventV2
from app.api.products import _feed_scored_items
from app.services.product_identity import product_identity_keys


class FeedRepeatTests(TestCase):
    def test_expired_cooldown_does_not_restore_recent_feedback_and_users_are_isolated(self):
        engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(engine)
        now = datetime.now(timezone.utc)
        with Session(engine) as db:
            user = User(id='u', email='u@test.test', password_hash='x')
            db.add(user)
            for n, uid, kind, days in [
                (1, 'u', 'like', 2), (2, 'u', 'skip', 2), (3, 'u', 'save', 2),
                (4, 'other', 'like', 2), (5, 'u', 'view', 2), (6, 'u', 'like', 31),
            ]:
                db.add(RecommendationEventV2(id=str(n), user_id=uid, product_id=f'p{n}',
                       event_type=kind, created_at=now-timedelta(days=days)))
            db.flush()
            with patch('app.services.activation_service.require_warm_feed'), \
                 patch('app.api.products.generate_feed', return_value=[]) as generate:
                _feed_scored_items(db, user, limit=20, exclude_ids=['queued'])
            self.assertEqual(generate.call_args.kwargs['exclude_product_ids'], {'p1', 'p2', 'p3', 'queued'})
        engine.dispose()

    def test_same_merchant_model_with_different_size_photos_is_one_identity(self):
        def keys(pid, url):
            return product_identity_keys(Product(id=pid, original_url=url, image_url=f'https://cdn.test/{pid}.jpg'))
        first = keys('a', 'https://www.shop.test/p/coat/?size=M&utm_source=feed&color=black')
        self.assertTrue(first & keys('b', 'https://shop.test/p/coat?color=black&size=L'))
        self.assertFalse(first & keys('c', 'https://shop.test/p/coat?color=red'))
        self.assertFalse(keys('d', 'https://shop.test/product?id=1') & keys('e', 'https://shop.test/product?id=2'))
