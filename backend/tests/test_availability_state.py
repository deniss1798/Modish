import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db import Base
from app.models import Product
from app.schemas.api_product import product_to_api
from app.services.catalog.stock_freshness import availability_snapshot, available_product_conditions


class AvailabilityStateTests(unittest.TestCase):
    def test_read_snapshot_and_sql_agree_without_renewing_stock(self):
        now = datetime.now(timezone.utc)
        engine = create_engine('sqlite:///:memory:')
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        cases = [
            ({}, 'available'),
            ({'availability_status': None}, 'unknown'),
            ({'availability_status': 'unknown'}, 'unknown'),
            ({'availability_status': 'IN_STOCK'}, 'unknown'),
            ({'availability_status': 'out_of_stock'}, 'unavailable'),
            ({'is_available': 0}, 'unavailable'),
            ({'is_active': 0}, 'unavailable'),
            ({'is_deleted_from_feed': 1}, 'unavailable'),
            ({'last_seen_in_feed_at': None}, 'unknown'),
            ({'last_seen_in_feed_at': now - timedelta(hours=73)}, 'stale'),
            ({'last_seen_in_feed_at': now + timedelta(hours=1)}, 'unknown'),
        ]
        with Session(engine) as db:
            expected = set()
            for index, (changes, state) in enumerate(cases):
                fields = dict(id=str(index), external_id=str(index), source='shop', title='Рубашка', brand='brand',
                    category='рубашки', price=1000, image_url='https://shop.test/p.jpg',
                    product_url='https://shop.test/p', is_active=1, is_available=1, is_deleted_from_feed=0,
                    availability_status='in_stock', last_seen_in_feed_at=now, available_sizes=['M'])
                fields.update(changes)
                product = Product(**fields)
                db.add(product)
                result = availability_snapshot(product, checked_at=now)
                self.assertEqual(result.state, state)
                self.assertIsNone(result.confidence)
                self.assertEqual(product.last_seen_in_feed_at, fields['last_seen_in_feed_at'])
                self.assertEqual(product_to_api(product)['availability_state'], state)
                if state == 'available':
                    expected.add(product.id)
            db.flush()
            actual = set(db.scalars(select(Product.id).where(*available_product_conditions(checked_at=now))))
            self.assertEqual(actual, expected)
