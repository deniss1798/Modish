"""Semantic optimizations preserve evidence, scores and full-catalog winners."""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, insert
from sqlalchemy.orm import Session

from app.db import Base
from app.models import Product, TasteProfile, User
from app.services import activation_service as activation
from app.services.catalog.product_dna import (
    RULE_VERSION, build_product_dna, reliable_taste_features,
)
from app.services.personalization_scoring import AffinityScorer
from app.services.recommendation_context import ShoppingContext
from app.services.user_twin_service import DNA_TASTE_PREFIX


class SemanticRankingPerformanceTests(unittest.TestCase):
    def scoring_product(self, **changes):
        values = dict(id='semantic-product', title='Рубашка', category='рубашки',
            category_name='рубашки', brand='BAON', price=2000, colors=['black'],
            style_tags=['casual'], feed_raw_json={}, raw_params_json={})
        values.update(changes)
        return Product(**values)

    def test_reduced_dna_preserves_full_evaluation_with_positive_negative_and_unknown_evidence(self):
        profile = SimpleNamespace(style_preferences=['casual', 'minimalism'])
        taste = TasteProfile(category_weights={'рубашки': 3}, style_weights={'casual': 2},
            color_weights={'black': -1}, brand_weights={'baon': 2})
        products = [
            self.scoring_product(title='Хлопковая рубашка прямого кроя',
                feed_raw_json={'material': 'cotton', 'fit': 'slim', 'silhouette': 'straight',
                    'pattern': 'striped', 'length': 'midi', 'sleeve': 'long', 'texture': 'ribbed',
                    'occasion': 'work', 'style': 'business', 'season': 'winter', 'formality': 'formal'}),
            self.scoring_product(title='Хлопковая рубашка',
                feed_raw_json={'material': 'wool', 'fit': 'slim', 'season': 'winter', 'occasion': 'evening'},
                raw_params_json={'Посадка': 'oversized', 'Сезон': 'summer'}),
            self.scoring_product(title='Шерстяная рубашка',
                feed_raw_json={'material': '100% cotton, 0% wool', 'pattern': 'not striped',
                    'style': ['sporty', 'business'], 'formality': 'smart casual'}),
            self.scoring_product(material='cotton', fit='slim', season='winter',
                description='Шелковая приталенная рубашка в офис',
                feed_raw_json={'unrecognized_attribute': 'cotton slim winter'}),
            self.scoring_product(feed_raw_json={'material': 'linen', 'fit': 'relaxed',
                'silhouette': 'wide', 'pattern': 'floral', 'length': 'maxi', 'season': 'summer',
                'occasion': 'evening', 'formality': 'casual'}),
        ]
        feature_maps = [
            {('material', DNA_TASTE_PREFIX + 'cotton'): (.9, .8),
             ('material', DNA_TASTE_PREFIX + 'wool'): (-.8, .9),
             ('fit', DNA_TASTE_PREFIX + 'slim'): (-.7, .9),
             ('silhouette', DNA_TASTE_PREFIX + 'straight'): (.6, .7)},
            {('pattern', DNA_TASTE_PREFIX + 'striped'): (-.9, .8),
             ('pattern', DNA_TASTE_PREFIX + 'floral'): (.8, .9)},
            {('category', 'рубашки'): (.6, .7), ('brand', 'baon'): (-.5, .8),
             ('item', 'semantic-product'): (.4, .6)},
            {},
        ]
        contexts = [None, ShoppingContext('office', season='winter'),
            ShoppingContext('evening', season='summer'), ShoppingContext('daily', budget_max=1000)]
        for product_index, product in enumerate(products):
            for feature_index, learned in enumerate(feature_maps):
                for context in contexts:
                    with self.subTest(product=product_index, learned=feature_index, context=context):
                        reduced = AffinityScorer(profile, taste, taste_features=learned,
                            context=context).evaluate(product)
                        # Reference the former all-dimension read, retaining real
                        # evidence rules, context logic and scoring calculations.
                        with patch('app.services.personalization_scoring.reliable_taste_features',
                                side_effect=lambda value, **_kwargs: reliable_taste_features(value)):
                            full = AffinityScorer(profile, taste, taste_features=learned,
                                context=context).evaluate(product)
                        self.assertEqual(reduced, full)
        positive = AffinityScorer(profile, taste,
            taste_features={('material', DNA_TASTE_PREFIX + 'cotton'): (1, 1)})
        negative = AffinityScorer(profile, taste,
            taste_features={('material', DNA_TASTE_PREFIX + 'cotton'): (-1, 1)})
        self.assertGreater(positive.evaluate(products[0]).affinity, negative.evaluate(products[0]).affinity)

    def test_semantic_reads_do_not_access_commerce_properties(self):
        class SemanticOnlyProduct:
            title = 'Хлопковая рубашка'
            feed_raw_json = {'material': 'cotton', 'fit': 'slim'}
            raw_params_json = {}

            @property
            def price(self):
                raise AssertionError('A semantic read must not load commerce fields')

            @property
            def available_sizes(self):
                raise AssertionError('A semantic read must not load size evidence')

        value = SemanticOnlyProduct()
        self.assertEqual(reliable_taste_features(value), {'material': {'cotton'}, 'fit': {'slim'}})
        self.assertEqual(reliable_taste_features(value, dimensions=('material',)), {'material': {'cotton'}})
        with self.assertRaisesRegex(AssertionError, 'commerce fields'):
            build_product_dna(value)

    def test_full_dna_retains_commerce_snapshot_and_exact_evidence_provenance(self):
        observed_at = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)
        value = SimpleNamespace(id='snapshot-product', title='Хлопковая рубашка',
            price=Decimal('1999.50'), currency='RUB', available_sizes=['M'],
            available_sizes_detailed=[{'raw_size': 'M', 'availability': 'available'}],
            last_seen_in_feed_at=observed_at, is_available=1, availability_status='in_stock',
            material='cotton', feed_raw_json={'material': 'cotton'},
            raw_params_json={'param_Состав': '100% хлопок'})
        before = deepcopy(vars(value))
        dna = build_product_dna(value)
        commerce = dna['commerce']
        self.assertEqual(commerce['provenance'], 'model_snapshot_unverified')
        self.assertEqual({name: commerce['fields'][name] for name in (
            'price', 'currency', 'available_sizes', 'available_sizes_detailed',
            'last_seen_in_feed_at', 'is_available', 'availability_status', 'size_system')},
            {'price': '1999.50', 'currency': 'RUB', 'available_sizes': ['M'],
             'available_sizes_detailed': [{'raw_size': 'M', 'availability': 'available'}],
             'last_seen_in_feed_at': observed_at.isoformat(), 'is_available': 1,
             'availability_status': 'in_stock', 'size_system': None})
        row = dna['semantic']['material'][0]
        self.assertEqual((row['value'], row['confidence'], row['status']), ('cotton', .95, 'supported'))
        self.assertEqual(row['rule_id'], 'assertion.material.cotton')
        self.assertEqual(row['rule_version'], RULE_VERSION)
        self.assertEqual([(item['source'], item['field'], item['raw_value'], item['matched_text'], item['confidence'])
            for item in row['evidence']], [
                ('raw_attribute', 'feed_raw_json.material', 'cotton', 'cotton', .95),
                ('raw_attribute', 'raw_params_json.param_Состав', '100% хлопок', 'хлопок', .95),
                ('sold_item_title', 'title', 'хлопковая рубашка', 'хлопковая', .8),
                ('legacy_model', 'material', 'cotton', 'cotton', .35),
            ])
        self.assertTrue(all(item['rule_version'] == RULE_VERSION for item in row['evidence']))
        self.assertEqual(vars(value), before)
        commerce['fields']['available_sizes'].append('L')
        commerce['fields']['available_sizes_detailed'][0]['availability'] = 'unavailable'
        self.assertEqual(vars(value), before)

    def test_distinct_raw_fields_with_the_same_normalized_key_keep_conflicting_evidence(self):
        value = self.scoring_product(feed_raw_json={'material': 'cotton', 'param_material': 'wool'})
        dna = build_product_dna(value)
        self.assertEqual(reliable_taste_features(value, dimensions=('material',)), {})
        self.assertEqual([(row['value'], row['status'], row['evidence'][0]['field'])
            for row in dna['semantic']['material']], [
                ('cotton', 'conflict', 'feed_raw_json.material'),
                ('wool', 'conflict', 'feed_raw_json.param_material'),
            ])

    def test_large_catalog_keeps_early_and_late_winners_without_final_sort_rescoring(self):
        engine = create_engine('sqlite:///:memory:')
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        now = datetime.now(timezone.utc)
        with Session(engine, autoflush=False) as db:
            user = User(id='semantic-performance-user', email='performance@example.test', password_hash='unused')
            db.add(user); db.flush()
            session = activation.configure(db, user, age_group='25-34', height=170,
                clothing_size='M', clothing_size_system='LETTER', shoe_size='38', shoe_size_system='EU',
                styles=['casual', 'minimalism'], price_segment='mass')
            profile, fit, taste = activation._preferences(db, user)
            early_id, late_id = f'{0:036d}', 'z' * 36
            rows = []
            for index in range(1201):
                pid = late_id if index == 1200 else f'{index:036d}'
                rows.append(dict(id=pid, external_id=pid, source='baon', brand='BAON',
                    title='Рубашка', category='рубашки', price=2000, currency='RUB',
                    gender_target='womenswear', available_sizes=['M'], colors=['black'], style_tags=['casual'],
                    feed_raw_json={'size_system': 'LETTER', 'material': 'cotton' if pid in {early_id, late_id} else 'linen'},
                    image_url=f'https://catalog.example.test/{pid}.jpg',
                    product_url=f'https://catalog.example.test/p/{pid}',
                    is_active=1, is_available=1, availability_status='in_stock', is_deleted_from_feed=0,
                    last_seen_in_feed_at=now))
            wrong_id = 'wrong-size-late-cotton'
            rows.append({**rows[-1], 'id': wrong_id, 'external_id': wrong_id, 'available_sizes': ['L'],
                'image_url': f'https://catalog.example.test/{wrong_id}.jpg'})
            db.execute(insert(Product), rows); db.flush()
            learned = {('material', DNA_TASTE_PREFIX + 'cotton'): (1, 1),
                       ('material', DNA_TASTE_PREFIX + 'linen'): (-1, 1)}
            scorer = AffinityScorer(profile, taste, taste_features=learned)
            counts = Counter()
            original_score = scorer.score

            def counted_score(product):
                counts[product.id] += 1
                return original_score(product)

            with patch.object(scorer, 'score', side_effect=counted_score):
                pool = activation.candidate_pool(db, user, session, taste_features=learned,
                    scorer=scorer, preferences=(profile, fit, taste))
            self.assertEqual(len(pool), 50)
            self.assertEqual([product.id for product in pool[:2]], [early_id, late_id])
            self.assertNotIn(wrong_id, counts)
            self.assertNotIn(wrong_id, {product.id for product in pool})
            self.assertEqual(len(counts), 1201, 'All eligible products must reach ranking, including IDs beyond 1000.')
            self.assertEqual(set(counts.values()), {1}, 'Final sorting must reuse scores of selected products.')
            self.assertLessEqual(scorer.cached_products, 512)


if __name__ == '__main__':
    unittest.main()
