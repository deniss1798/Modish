from fit_fixtures import confirmed_fit
"""Stock expiry applies while serving, without a successful scheduled import."""
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import FitProfile, Outfit, Product, ProductSource, TasteProfile, User
from app.api.outfits import visible_outfits
from app.api.products import products_brands, products_get, products_list
from app.schemas.api_product import product_to_api
from app.services.catalog.catalog_quality import product_is_feed_eligible
from app.services.catalog.catalog_records import reprocess_records, store_record
from app.services.catalog.quality_gate import evaluate_product
from app.services.catalog.stock_freshness import fresh_stock_conditions, stock_observation_problem
from app.services.candidate_retrieval_service import retrieve_candidates
from app.services.embedding_service import DEFAULT_VECTOR_STORE, embed_text, product_embedding_text, upsert_product_embedding
from app.services.outfit_service import generate_outfits
from app.services.product_complements import complementary_products
from app.services.recommendation_engine import generate_feed


class StockFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, autoflush=False)()
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        self.now = datetime.now(timezone.utc)
        self.user = User(id=str(uuid4()), email='freshness@example.test', password_hash='unused')
        self.fit = confirmed_fit(id=str(uuid4()), user_id=self.user.id, height_cm=170,
            gender_target='womenswear', clothing_size='M', budget_max=25000)
        self.taste = TasteProfile(id=str(uuid4()), user_id=self.user.id)
        self.db.add_all([self.user, self.fit, self.taste])
        self.db.flush()

    def product(self, title='Женская рубашка', category='рубашки', **changes):
        pid = str(uuid4())
        values = dict(id=pid, external_id=pid, source='baon', title=title, brand='BAON',
            category=category, price=2000, currency='RUB', image_url=f'https://shop.example/{pid}.jpg',
            product_url=f'https://shop.example/{pid}', gender_target='womenswear',
            available_sizes=['M'], size_system='INT', colors=['black'], style_tags=['casual'],
            is_active=1, is_available=1, availability_status='in_stock', is_deleted_from_feed=0,
            last_seen_in_feed_at=self.now, created_at=self.now, updated_at=self.now)
        values.update(changes)
        row = Product(**values)
        self.db.add(row)
        self.db.flush()
        return row

    def test_sql_admission_and_read_gate_share_boundaries_including_missing_and_future(self):
        expected = set()
        observations = [None, self.now, self.now - timedelta(hours=72),
            self.now - timedelta(hours=72, microseconds=1),
            self.now + timedelta(minutes=5), self.now + timedelta(minutes=5, microseconds=1),
            self.now.replace(tzinfo=None)]
        self.assertIsNone(stock_observation_problem(SimpleNamespace(last_seen_in_feed_at=
            self.now.astimezone(timezone(timedelta(hours=3)))), checked_at=self.now))
        for observed in observations:
            # Feed observations are stored in UTC, including SQLite tests.
            row = self.product(last_seen_in_feed_at=observed)
            problem = stock_observation_problem(row, checked_at=self.now)
            self.assertEqual(product_is_feed_eligible(row, checked_at=self.now), problem is None)
            self.assertEqual(evaluate_product(row, checked_at=self.now).status == 'passed', problem is None)
            if problem is None:
                expected.add(row.id)
        self.db.flush()
        self.assertEqual(set(self.db.scalars(select(Product.id).where(
            *fresh_stock_conditions(checked_at=self.now)))), expected)

    def test_expiry_is_read_only_and_does_not_trust_recent_processing(self):
        row = self.product(last_seen_in_feed_at=self.now-timedelta(hours=73),
            updated_at=self.now, last_checked_at=self.now)
        self.assertFalse(product_is_feed_eligible(row))
        card = products_get(row.id, self.db)
        self.assertFalse(card['is_available'])
        self.assertEqual(card['availability_status'], 'unknown')
        self.assertEqual(card['availability_reason'], 'stale_availability')
        self.assertEqual(row.is_active, 1)
        self.assertEqual(row.is_available, 1)
        self.assertEqual(row.last_seen_in_feed_at, self.now-timedelta(hours=73))
        self.assertEqual(row.availability_status, 'in_stock')
        for source in ('legacy-unregistered', 'demo', 'baon'):
            row.source, row.last_seen_in_feed_at = source, None
            self.assertFalse(product_is_feed_eligible(row))
            self.assertFalse(product_to_api(row)['is_available'])

    def test_feed_search_brands_and_candidates_exclude_old_stock_before_limits(self):
        stale = [self.product(brand='Stale', last_seen_in_feed_at=self.now-timedelta(days=4)) for _ in range(310)]
        good = self.product(brand='Fresh', updated_at=self.now-timedelta(days=10),
            created_at=self.now-timedelta(days=10))
        missing = self.product(brand='Missing', last_seen_in_feed_at=None)
        future = self.product(brand='Future', last_seen_in_feed_at=self.now+timedelta(hours=1))
        self.db.flush()
        retrieved = retrieve_candidates(self.db, user=self.user, fit=self.fit, taste=self.taste,
            taste_features={}, max_candidates=50)
        self.assertEqual([p.id for p in retrieved.products], [good.id])
        self.assertEqual([p['id'] for p in products_list(limit=1, db=self.db, credentials=None)], [good.id])
        self.assertEqual(products_brands(db=self.db), [{'brand': 'Fresh', 'count': 1}])
        self.assertEqual([s.product.id for s in generate_feed(self.db, self.user)], [good.id])
        # Alternate candidate providers must still pass the central read gate.
        with patch('app.services.candidate_retrieval_service.retrieve_embedding_candidates',
                   return_value=[stale[0], missing, future]):
            retrieved = retrieve_candidates(self.db, user=self.user, fit=self.fit,
                taste=self.taste, taste_features={})
        self.assertEqual([p.id for p in retrieved.products], [good.id])

    def test_embedding_lookup_excludes_expired_observations(self):
        good = self.product()
        stale = self.product(last_seen_in_feed_at=self.now-timedelta(days=4))
        for row in (good, stale):
            upsert_product_embedding(self.db, row)
        results = DEFAULT_VECTOR_STORE.search_similar_products(self.db,
            query_embedding=embed_text(product_embedding_text(good)), limit=20)
        self.assertEqual([hit.product_id for hit in results], [good.id])

    def test_existing_outfits_and_complements_expire_with_stock(self):
        top = self.product()
        bottom = self.product('Брюки женские', 'брюки')
        shoes = self.product('Ботинки женские', 'обувь')
        outfit = Outfit(id=str(uuid4()), user_id=self.user.id,
            items_json={'top': top.id, 'bottom': bottom.id, 'shoes': shoes.id},
            total_price=6000, style_direction='daily', is_saved=1)
        self.db.add(outfit)
        self.db.flush()
        self.assertEqual(len(visible_outfits(self.db, self.user, [outfit])), 1)
        self.assertIn(shoes.id, [p.id for p in complementary_products(self.db, self.user, top)])
        shoes.last_seen_in_feed_at = self.now - timedelta(days=4)
        self.db.flush()
        self.assertEqual(visible_outfits(self.db, self.user, [outfit]), [])
        self.assertNotIn(shoes.id, [p.id for p in complementary_products(self.db, self.user, top)])
        top.last_seen_in_feed_at = self.now - timedelta(days=4)
        self.assertEqual(complementary_products(self.db, self.user, top), [])
        self.assertEqual(outfit.is_saved, 1)

    def test_legacy_outfit_fallback_cannot_reintroduce_expired_products(self):
        for title, category in [('Рубашка женская', 'рубашки'), ('Брюки женские', 'брюки'), ('Ботинки женские', 'обувь')]:
            self.product(title, category, last_seen_in_feed_at=self.now-timedelta(days=4))
        self.assertEqual(generate_outfits(self.db, self.user, use_v2=False), [])

    def test_only_new_source_observation_restores_expired_product(self):
        source = ProductSource(id='source', code='baon', name='BAON', network='direct')
        self.db.add(source)
        self.db.flush()
        offer = dict(id='fresh-1', name='Женская рубашка', category='рубашки', gender='женский',
            price='2000', currency='RUB', sizes='M', available=True,
            picture='https://shop.example/image.jpg', url='https://shop.example/product/fresh-1')
        old = self.now - timedelta(days=4)
        record, _ = store_record(self.db, source, offer, old)
        row = self.db.get(Product, record.product_id)
        original_id = row.id
        self.assertFalse(product_is_feed_eligible(row))
        reprocess_records(self.db, source, dry_run=False)
        self.assertEqual(row.last_seen_in_feed_at, old)
        self.assertFalse(product_is_feed_eligible(row))
        record, _ = store_record(self.db, source, offer, self.now)
        self.assertEqual(record.product_id, original_id)
        self.assertTrue(product_is_feed_eligible(row))


if __name__ == '__main__':
    unittest.main()
