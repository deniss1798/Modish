from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Product, ProductSource
from app.money import as_rubles, json_rubles, parse_rubles
from app.scripts import import_products
from app.services.catalog.catalog_records import reprocess_records, store_record
from app.services.catalog.feed_row_mapper import _int_price
from app.schemas.shopping_intent import IntentSpec
from app.services.shopping_intent_parser import parse_intent
from test_catalog_foundation import offer
from datetime import datetime, timedelta, timezone


class MoneyParserTests(unittest.TestCase):
    def test_numeric_types_share_exact_ruble_and_kopeck_contract(self):
        for raw in (1299, 1299.0, '1299', '1299.00', Decimal('1299')):
            with self.subTest(raw=raw):
                amount = parse_rubles(raw)
                self.assertIsInstance(amount, Decimal)
                self.assertEqual(amount, Decimal('1299.00'))
                self.assertEqual(amount.as_tuple().exponent, -2)
        for raw in (1299.50, '1299,50', '1299.50', Decimal('1299.50')):
            with self.subTest(raw=raw):
                self.assertEqual(parse_rubles(raw), Decimal('1299.50'))
                self.assertEqual(_int_price(raw), Decimal('1299.50'))

    def test_rounds_half_up_once_to_kopeck_for_all_numeric_forms(self):
        for raw in (12.345, '12.345', '12,345', Decimal('12.345')):
            self.assertEqual(parse_rubles(raw), Decimal('12.35'))
        self.assertEqual(parse_rubles('0.005'), Decimal('0.01'))
        self.assertEqual(parse_rubles('1.004'), Decimal('1.00'))
        self.assertEqual(parse_rubles(0), Decimal('0.00'))

    def test_explicit_grouping_and_currency_suffixes(self):
        for raw in ('1 299,50', '1\u00a0299.50', '1\u202f299,50',
                    '1,299.50', '1.299,50', '1299,50 ₽', '1299.50 rub', '1299.50 RUR'):
            self.assertEqual(parse_rubles(raw), Decimal('1299.50'), raw)
        self.assertEqual(parse_rubles('1,299,000'), Decimal('1299000.00'))
        self.assertEqual(parse_rubles('1.299.000'), Decimal('1299000.00'))
        self.assertEqual(parse_rubles('1e3'), Decimal('1000.00'))

    def test_rejects_invalid_negative_nonfinite_and_ambiguous_inputs(self):
        class Unprintable:
            def __str__(self):
                raise AssertionError('Arbitrary objects must not be formatted')
        for raw in (None, True, False, -1, -0.0, '-0', '-1,50', float('nan'),
                    float('inf'), Decimal('-Infinity'), 'NaN', 'Infinity',
                    '', 'call', '1RUB2', '12 34,50', '1,29.50', '1,2,3',
                    ['1299'], {'price': 1299}, Unprintable(), '9' * 129):
            self.assertIsNone(parse_rubles(raw), type(raw).__name__)
        self.assertIsNone(parse_rubles('10000000.01'))
        self.assertEqual(parse_rubles('10000000'), Decimal('10000000.00'))

    def test_json_numbers_stay_rubles_and_exact_arithmetic_avoids_float_sum(self):
        self.assertEqual(as_rubles('0.10') + as_rubles('0.20'), Decimal('0.30'))
        self.assertEqual(json_rubles(Decimal('1299.50')), 1299.5)
        self.assertIsInstance(json_rubles(Decimal('1299.00')), int)
        # Totals can exceed an individual product's admission cap.
        self.assertEqual(json_rubles(Decimal('20000000.01')), 20000000.01)
        self.assertEqual(json.loads(json.dumps({'price': json_rubles('12.99')})), {'price': 12.99})

    def test_cheaper_intent_accepts_decimal_reference_without_changing_whole_ruble_budget(self):
        for reference in (Decimal('1299.99'), 1299.99):
            result = parse_intent('дешевле', previous=IntentSpec(
                requested_categories=['брюки'], budget_max=5000),
                reference_price=reference, default_budget=4000)
            self.assertEqual(result.budget_max, 1039)
            self.assertIsInstance(result.budget_max, int)
            self.assertEqual(result.unresolved, [])


class MoneyPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db = self.sessions()
        self.source = ProductSource(id='money-source', code='baon', name='BAON', network='direct')
        self.db.add(self.source)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_catalog_keeps_kopecks_in_orm_and_json_and_preserves_ids_on_replay(self):
        raw = offer(price='1299,50', oldprice='1499,99')
        now = datetime.now(timezone.utc)
        record, action = store_record(self.db, self.source, raw, now)
        self.db.commit()
        product_id = record.product_id
        self.assertEqual(action, 'created')
        self.db.expire_all()
        product = self.db.get(Product, product_id)
        self.assertEqual(product.price, Decimal('1299.50'))
        self.assertEqual(product.old_price, Decimal('1499.99'))
        self.assertEqual(record.raw_json, raw)
        self.assertEqual(record.canonical_json['price'], 1299.50)
        self.assertEqual(record.canonical_json['old_price'], 1499.99)
        json.dumps(record.canonical_json)  # No Decimal leaks into SQL JSON.
        replay, action = store_record(self.db, self.source, offer(price='1300.01'), now)
        self.db.commit()
        self.assertEqual(action, 'updated')
        self.assertEqual(replay.product_id, product_id)
        self.db.expire_all()
        self.assertEqual(self.db.get(Product, product_id).price, Decimal('1300.01'))

    def test_manual_csv_and_json_import_share_parser_and_upsert_cannot_truncate(self):
        with tempfile.TemporaryDirectory() as directory:
            csv_path = Path(directory) / 'products.csv'
            csv_path.write_text('external_id;title;category;price;old_price;image_url;product_url\n'
                'manual;Блузка;Рубашки;1299,50;1499,99;https://baon.ru/a.jpg;https://baon.ru/a\n', encoding='utf-8')
            rows = import_products.load_csv(csv_path)
            self.assertEqual(rows[0]['price'], Decimal('1299.50'))
            with patch.object(import_products, 'SessionLocal', self.sessions):
                self.assertEqual(import_products.upsert_products(rows), (1, 0))
                item = import_products._json_item_normalized({**rows[0], 'price': '1300.01'})
                self.assertEqual(import_products.upsert_products([item]), (0, 1))
            self.db.expire_all()
            product = self.db.scalar(select(Product).where(Product.external_id == 'manual'))
            self.assertEqual(product.price, Decimal('1300.01'))
            self.assertEqual(product.old_price, Decimal('1499.99'))

    def test_stored_raw_replay_recovers_old_rounded_price_without_fresh_stock_or_new_id(self):
        observed = datetime.now(timezone.utc) - timedelta(hours=2)
        record, _ = store_record(self.db, self.source, offer(price='1299,50', oldprice='1499,99'), observed)
        product_id = record.product_id
        product = self.db.get(Product, product_id)
        # Simulate a pre-v7 whole-ruble snapshot. The raw merchant facts survive.
        product.price, product.old_price = Decimal('1300.00'), Decimal('1500.00')
        record.canonical_json = {**record.canonical_json, 'price': 1300, 'old_price': 1500}
        record.normalization_version = 'catalog_rules_v6'
        self.db.commit()
        result = reprocess_records(self.db, self.source, dry_run=False)
        self.db.commit()
        self.db.expire_all()
        product = self.db.get(Product, product_id)
        record = self.db.get(type(record), record.id)
        self.assertEqual(result['processed'], 1)
        self.assertEqual(record.product_id, product_id)
        self.assertEqual(product.price, Decimal('1299.50'))
        self.assertEqual(product.old_price, Decimal('1499.99'))
        # Processing now is not a new observation of merchant availability.
        self.assertEqual(product.last_seen_in_feed_at.replace(tzinfo=timezone.utc), observed)
        self.assertEqual(record.last_seen_at.replace(tzinfo=timezone.utc), observed)
        self.assertEqual(record.normalization_version, 'catalog_rules_v7')
