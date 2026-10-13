"""DNA is consumed by import/replay and the same taste keys used by MIE."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import CatalogRecord, Product, ProductSource, TasteProfile, User, UserTasteFeature
from app.services.catalog.catalog_records import store_record
from app.services.catalog.product_dna import RULE_VERSION
from app.services.catalog.size_evidence import confirmed_product_size
from app.services.mie_scoring import compute_taste_score, load_user_taste_feature_map
from app.services.user_twin_service import DNA_TASTE_PREFIX, apply_event_to_user_twin, product_taste_features


class ProductDNAIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, autoflush=False)()
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        self.source = ProductSource(id='dna-source', code='baon', name='BAON', network='direct')
        self.user = User(id=str(uuid4()), email='dna@example.test', password_hash='unused')
        self.db.add_all([self.source, self.user])
        self.db.flush()
        self.now = datetime.now(timezone.utc)

    def offer(self, **changes):
        return {'id': str(uuid4()), 'name': 'Женская рубашка приталенная',
                'gender': 'женский', 'category': 'Рубашки', 'price': '3999.00',
                'currency': 'RUB', 'sizes': 'M', 'available': True,
                'picture': 'https://shop.example/image.jpg', 'url': 'https://shop.example/product',
                'param_состав': '100% хлопок', **changes}

    def store(self, raw, **kwargs):
        record, _ = store_record(self.db, self.source, raw, self.now, **kwargs)
        return record, self.db.get(Product, record.product_id) if record.product_id else None

    def test_import_retains_evidence_and_replay_keeps_identity_and_stock_age(self):
        raw = self.offer()
        original = deepcopy(raw)
        observed = self.now - timedelta(hours=3)
        record, product = self.store(raw, last_seen_at=observed)
        product_id, record_id = product.id, record.id
        dna = deepcopy(record.canonical_json['product_dna'])
        self.assertEqual(dna['rules_version'], RULE_VERSION)
        cotton = next(row for row in dna['semantic']['material'] if row['value'] == 'cotton')
        self.assertEqual(cotton['status'], 'supported')
        self.assertTrue(any(row['field'] == 'feed_raw_json.param_состав' for row in cotton['evidence']))
        self.assertEqual(product.material, '100% хлопок')
        self.assertEqual(product_taste_features(product)['material'], {DNA_TASTE_PREFIX + 'cotton'})
        self.assertEqual(product_taste_features(product)['fit'], {DNA_TASTE_PREFIX + 'slim'})
        self.db.commit()
        replay, _ = self.store(raw, last_seen_at=observed)
        self.assertEqual(replay.id, record_id)
        self.assertEqual(replay.product_id, product_id)
        self.assertEqual(replay.canonical_json['product_dna'], dna)
        self.assertEqual(replay.last_seen_at, observed)
        self.assertEqual(raw, original)

    def test_real_event_learns_material_and_mie_consumes_the_same_key(self):
        _, cotton = self.store(self.offer())
        _, linen = self.store(self.offer(param_состав='100% лен'))
        for _ in range(5):
            apply_event_to_user_twin(self.db, user_id=self.user.id, product=cotton, event_type='like')
        learned = load_user_taste_feature_map(self.db, user_id=self.user.id)
        self.assertIn(('material', DNA_TASTE_PREFIX + 'cotton'), learned)
        self.assertNotIn(('material', '100% хлопок'), learned)
        # Isolate the material signal from the equally learned category/brand.
        material_signal = {key: value for key, value in learned.items() if key[0] == 'material'}
        taste = self.db.scalar(select(TasteProfile).where(TasteProfile.user_id == self.user.id))
        cotton_score = compute_taste_score(cotton, taste=taste, taste_features=material_signal)[0]
        linen_score = compute_taste_score(linen, taste=taste, taste_features=material_signal)[0]
        self.assertGreater(cotton_score, linen_score)

    def test_unverified_legacy_construction_does_not_train_new_preferences(self):
        product = Product(id=str(uuid4()), external_id='legacy', source='baon', title='Рубашка',
            brand='BAON', category='рубашки', price=3000, material='wool', fit='slim',
            silhouette='straight', style_tags=['casual'], colors=['black'],
            product_url='https://shop.example/item', image_url='https://shop.example/item.jpg')
        self.db.add(product)
        self.db.flush()
        apply_event_to_user_twin(self.db, user_id=self.user.id, product=product, event_type='like')
        rows = list(self.db.scalars(select(UserTasteFeature).where(UserTasteFeature.user_id == self.user.id)))
        self.assertTrue(rows)
        self.assertFalse({'material', 'fit', 'silhouette'} & {row.feature_type for row in rows})
        self.assertEqual(product_taste_features(product)['style'], {'casual'})

    def test_semantic_evidence_cannot_validate_stock_or_numeric_size(self):
        record, product = self.store(self.offer(sizes='42', available=False,
            name='Хлопковая рубашка приталенная размера 42 в наличии'))
        self.assertEqual(record.status, 'unavailable')
        self.assertFalse(product.is_available)
        self.assertIsNone(product.size_system)
        self.assertFalse(confirmed_product_size(product, '42', requested_system='RU'))
        self.assertEqual(product_taste_features(product)['material'], {DNA_TASTE_PREFIX + 'cotton'})
        self.assertEqual(record.canonical_json['product_dna']['commerce']['provenance'], 'model_snapshot_unverified')

    def test_legacy_construction_confidence_is_not_inherited_by_new_dna_events(self):
        legacy = UserTasteFeature(id=str(uuid4()), user_id=self.user.id, feature_type='material',
            feature_value='cotton', preference_score=1.0, confidence=1.0,
            positive_count=100, negative_count=0)
        old_style = UserTasteFeature(id=str(uuid4()), user_id=self.user.id, feature_type='style',
            feature_value='casual', preference_score=0.5, confidence=0.8,
            positive_count=10, negative_count=0)
        self.db.add_all([legacy, old_style])
        self.db.flush()
        _, cotton = self.store(self.offer())
        before = load_user_taste_feature_map(self.db, user_id=self.user.id)
        self.assertNotIn(('material', 'cotton'), before)
        self.assertIn(('style', 'casual'), before)
        apply_event_to_user_twin(self.db, user_id=self.user.id, product=cotton, event_type='like')
        fresh = self.db.scalar(select(UserTasteFeature).where(
            UserTasteFeature.user_id == self.user.id,
            UserTasteFeature.feature_type == 'material',
            UserTasteFeature.feature_value == DNA_TASTE_PREFIX + 'cotton'))
        self.assertEqual(fresh.positive_count, 1)
        self.assertLess(fresh.confidence, 0.5)
        self.assertEqual(legacy.positive_count, 100)
        self.assertEqual(legacy.confidence, 1)


if __name__ == '__main__':
    unittest.main()
