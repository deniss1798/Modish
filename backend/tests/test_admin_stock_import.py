"""Admin imports need stock evidence and must fail before partial batch writes."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.api.admin_products import ProductIn, admin_products_import
from app.db import Base
from app.models import Product
from app.schemas.api_product import product_to_api
from app.services.catalog.catalog_quality import product_is_feed_eligible


class AdminStockImportTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, autoflush=False)()
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        self.now = datetime.now(timezone.utc)
        self.auth = patch.dict('os.environ', {'ADMIN_TOKEN': 'test-admin'})
        self.auth.start()
        self.addCleanup(self.auth.stop)

    def item(self, **changes):
        fields = dict(external_id='shirt-1', source='direct', title='Женская рубашка',
            brand='Test', category='рубашки', price=2500, currency='RUB',
            image_url='https://shop.example/shirt.jpg', product_url='https://shop.example/shirt',
            available_sizes=['M'], size_system='LETTER', gender_target='womenswear',
            stock_observed_at=self.now - timedelta(hours=1))
        fields.update(changes)
        return ProductIn(**fields)

    def import_items(self, items):
        return admin_products_import(items, self.db, 'Bearer test-admin')

    def row(self, external_id='shirt-1'):
        return self.db.scalar(select(Product).where(Product.external_id == external_id))

    def test_available_import_uses_explicit_observation_and_expires_from_that_time(self):
        item = self.item(stock_observed_at=(self.now - timedelta(hours=2)).astimezone(
            timezone(timedelta(hours=3))))
        self.assertEqual(self.import_items([item]), {'created': 1, 'updated': 0})
        row = self.row()
        observed = row.last_seen_in_feed_at.replace(tzinfo=timezone.utc)
        self.assertEqual(observed, item.stock_observed_at)
        self.assertEqual(row.availability_status, 'in_stock')
        self.assertTrue(product_is_feed_eligible(row))
        self.assertTrue(product_to_api(row)['is_available'])
        self.assertFalse(product_is_feed_eligible(row, checked_at=observed + timedelta(hours=73)))

    def test_invalid_stock_rejects_complete_batch_before_creating_or_editing_rows(self):
        self.import_items([self.item()])
        original = self.row()
        for observed, reason in ((None, 'unknown_stock_observation_time'),
                (self.now - timedelta(days=4), 'stale_availability'),
                (self.now + timedelta(hours=1), 'invalid_stock_observation_time')):
            with self.subTest(reason=reason):
                with self.assertRaises(HTTPException) as caught:
                    self.import_items([self.item(title='Changed'),
                        self.item(external_id='new-valid'), self.item(external_id='bad', stock_observed_at=observed)])
                self.assertEqual(caught.exception.status_code, 422)
                self.assertEqual(caught.exception.detail, {
                    'item_index': 2, 'field': 'stock_observed_at', 'reason': reason})
                self.assertEqual(original.title, 'Женская рубашка')
                self.assertFalse(self.db.new)
                self.assertFalse(self.db.dirty)
                self.assertEqual(self.db.scalar(select(func.count()).select_from(Product)), 1)

    def test_unavailable_imports_do_not_need_or_renew_stock_observation(self):
        self.import_items([self.item(is_available=False, stock_observed_at=None)])
        row = self.row()
        self.assertIsNone(row.last_seen_in_feed_at)
        self.assertFalse(product_to_api(row)['is_available'])
        self.assertEqual(row.availability_status, 'out_of_stock')
        self.import_items([self.item()])
        row = self.row()
        observed = row.last_seen_in_feed_at
        self.import_items([self.item(title='Edited unavailable shirt', is_available=False, stock_observed_at=None)])
        self.assertEqual(row.last_seen_in_feed_at, observed)
        self.assertEqual(row.availability_status, 'out_of_stock')
        self.assertFalse(product_is_feed_eligible(row))

    def test_new_observation_can_restore_a_previously_missing_offer(self):
        self.import_items([self.item()])
        row = self.row()
        original_id = row.id
        row.is_deleted_from_feed = 1
        row.is_active = 0
        row.is_available = 0
        self.db.commit()
        self.assertEqual(self.import_items([self.item(stock_observed_at=self.now)]), {'created': 0, 'updated': 1})
        self.assertEqual(row.id, original_id)
        self.assertEqual(row.is_deleted_from_feed, 0)
        self.assertTrue(product_is_feed_eligible(row))

    def test_observation_requires_an_explicit_timezone(self):
        with self.assertRaises(ValidationError):
            self.item(stock_observed_at=self.now.replace(tzinfo=None))


if __name__ == '__main__':
    unittest.main()
