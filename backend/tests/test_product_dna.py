"""Evidence rules must fail closed on ambiguity, without modifying products."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from types import SimpleNamespace
import unittest

from app.services.catalog.fashion_taxonomy import TAXONOMY
from app.services.catalog.product_dna import build_product_dna, reliable_taste_features


def product(**changes):
    fields = dict(title='Рубашка', price=3999, currency='RUB', available_sizes=['42'],
        size_system=None, availability_status='unknown', is_available=None,
        feed_raw_json={}, raw_params_json={}, style_tags=[], colors=[],
        material=None, season=None, fit=None, silhouette=None)
    fields.update(changes)
    return SimpleNamespace(**fields)


class ProductDNATests(unittest.TestCase):
    def test_unknown_stays_unknown_without_category_style_fallback(self):
        value = product(category='платья', brand='Some brand')
        dna = build_product_dna(value)
        self.assertEqual(set(dna['unknown_dimensions']), set(TAXONOMY))
        self.assertEqual(reliable_taste_features(value), {})
        self.assertIsNone(dna['commerce']['fields']['size_system'])
        self.assertEqual(dna['commerce']['fields']['availability_status'], 'unknown')

    def test_raw_attributes_have_exact_provenance_and_bounded_vocabulary(self):
        value = product(raw_params_json={'param_Состав': '100% хлопок', 'param_Посадка': 'slim fit'},
            feed_raw_json={'param_Силуэт': 'Прямой крой', 'param_Цвет': 'темно-синий'})
        features = reliable_taste_features(value)
        self.assertEqual(features['material'], {'cotton'})
        self.assertEqual(features['fit'], {'slim'})
        self.assertEqual(features['silhouette'], {'straight'})
        self.assertEqual(features['color'], {'navy'})
        row = build_product_dna(value)['semantic']['material'][0]
        self.assertEqual(row['evidence'][0]['field'], 'raw_params_json.param_Состав')
        self.assertEqual(row['evidence'][0]['raw_value'], '100% хлопок')
        self.assertEqual(row['status'], 'supported')

    def test_legacy_values_are_not_misrepresented_as_merchant_evidence(self):
        value = product(material='cotton', fit='slim', style_tags=['casual'])
        dna = build_product_dna(value)
        self.assertEqual(reliable_taste_features(value), {})
        for dimension in ('material', 'fit', 'style'):
            row = dna['semantic'][dimension][0]
            self.assertEqual(row['status'], 'unverified')
            self.assertEqual({e['source'] for e in row['evidence']}, {'legacy_model'})

    def test_description_and_companion_garments_do_not_supply_attributes(self):
        for title in ('Ремень для шерстяного платья', 'Рубашка с шелковым шарфом',
                      'Рубашка с кожаным ремнём', 'Рубашка со свободным жакетом'):
            with self.subTest(title=title):
                value = product(title=title, description='Сочетайте с шелковым платьем oversize')
                features = reliable_taste_features(value)
                self.assertNotIn('material', features)
                self.assertNotIn('fit', features)
        self.assertEqual(reliable_taste_features(product(title='Хлопковая рубашка с шелковым шарфом')),
                         {'material': {'cotton'}})

    def test_negative_evidence_blocks_positive_other_sources_in_both_directions(self):
        cases = [
            product(title='Шерстяной джемпер', raw_params_json={'Материал': 'Без шерсти'}),
            product(title='Рубашка без хлопка', feed_raw_json={'material': 'cotton'}),
            product(title='Рубашка не приталенная', feed_raw_json={'fit': 'slim'}),
        ]
        for value in cases:
            with self.subTest(title=value.title):
                dna = build_product_dna(value)
                dimension = 'fit' if 'приталенная' in value.title else 'material'
                self.assertNotIn(dimension, reliable_taste_features(value))
                self.assertTrue(dna['blocked_evidence'][dimension])
                self.assertFalse(any(row['status'] == 'supported' for row in dna['semantic'][dimension]))

    def test_zero_percentage_does_not_establish_material_or_override_title(self):
        for composition in ('0% шерсть', 'шерсть 0%', '0.00% wool', '100% хлопок, 0% шерсть'):
            with self.subTest(composition=composition):
                value = product(title='Шерстяной джемпер', feed_raw_json={'material': composition})
                self.assertNotIn('material', reliable_taste_features(value))
        self.assertEqual(reliable_taste_features(product(feed_raw_json={'material': '100% cotton'}))['material'], {'cotton'})

    def test_conflicting_sources_and_incompatible_values_are_not_guessed(self):
        cases = [
            product(feed_raw_json={'fit': 'slim'}, raw_params_json={'fit': 'oversize'}),
            product(title='Летняя рубашка', raw_params_json={'Сезон': 'зимний'}),
            product(title='Хлопковая рубашка', raw_params_json={'Материал': '100% шелк'}),
        ]
        for value in cases:
            self.assertEqual(reliable_taste_features(value), {})
        value = product(feed_raw_json={'style_tags': ['sporty', 'business']})
        self.assertNotIn('style', reliable_taste_features(value))

    def test_commerce_is_a_snapshot_not_semantic_inference(self):
        value = product(title='Зимняя шерстяная куртка размер EU 42 в наличии за 1000 рублей',
            price=3999, last_seen_in_feed_at=datetime(2026, 9, 24, tzinfo=timezone.utc))
        dna = build_product_dna(value)
        self.assertEqual(dna['commerce']['fields']['price'], 3999)
        self.assertIsNone(dna['commerce']['fields']['size_system'])
        self.assertIsNone(dna['commerce']['fields']['is_available'])
        self.assertEqual(dna['commerce']['provenance'], 'model_snapshot_unverified')

    def test_substrings_and_faux_leather_are_not_natural_leather(self):
        value = product(title='Куртка из искусственной кожи', feed_raw_json={'color': 'brownstone'})
        self.assertEqual(reliable_taste_features(value), {'material': {'faux_leather'}})

    def test_deterministic_serializable_nonmutating_and_dimension_selection(self):
        value = product(title='Рубашка приталенная', raw_params_json={'Материал': '100% хлопок'},
            last_seen_in_feed_at=datetime(2026, 9, 24, tzinfo=timezone.utc))
        before = deepcopy(vars(value))
        first = json.dumps(build_product_dna(value), ensure_ascii=False, sort_keys=True)
        second = json.dumps(build_product_dna(value), ensure_ascii=False, sort_keys=True)
        self.assertEqual(first, second)
        self.assertEqual(vars(value), before)
        self.assertEqual(reliable_taste_features(value, dimensions=('material',)), {'material': {'cotton'}})
        with self.assertRaises(ValueError):
            reliable_taste_features(value, dimensions=('price',))

    def test_merchant_cut_field_supplies_bounded_construction_evidence(self):
        value = product(title='Пуховик облегч. BAON', feed_raw_json={'param_крой': 'Прямой'})
        self.assertEqual(reliable_taste_features(value), {'silhouette': {'straight'}})
        row = build_product_dna(value)['semantic']['silhouette'][0]
        self.assertEqual(row['evidence'][0]['raw_value'], 'Прямой')
        self.assertEqual(row['evidence'][0]['field'], 'feed_raw_json.param_крой')
        value = product(raw_params_json={'Крой': 'Свободный'})
        self.assertEqual(reliable_taste_features(value), {'fit': {'relaxed'}})

    def test_reviewed_catalogue_construction_phrases(self):
        cases = [
            ('Брюки широкого кроя с контрастными лампасами по бокам', 'silhouette', 'wide'),
            ('Брюки прямого облегающего кроя с фигурной кокеткой', 'silhouette', 'straight'),
            ('Двубортный блейзер классического прямого кроя', 'silhouette', 'straight'),
            ('Блейзер приталенного кроя с бархатистой фактурой', 'fit', 'slim'),
            ('Вязаный пуловер прямого кроя', 'silhouette', 'straight'),
            ('Прямые брюки светло-лилового цвета', 'silhouette', 'straight'),
            ('Юбка-миди прямая с кружевом', 'silhouette', 'straight'),
            ('Платье-миди по фигуре с бантами на плечах', 'fit', 'slim'),
            ('Жилет свободного силуэта в оливковом цвете', 'fit', 'relaxed'),
            ('Жакет расслабленного силуэта жемчужно-серого цвета', 'fit', 'relaxed'),
            ('Жакет прилегающего силуэта с поясом', 'fit', 'slim'),
        ]
        for title, dimension, value in cases:
            with self.subTest(title=title):
                self.assertEqual(reliable_taste_features(product(title=title))[dimension], {value})

    def test_construction_does_not_follow_pockets_brand_names_or_companions(self):
        for title in ('Рубашка прямой воротник', 'Жакет широкие карманы',
                      'Брюки с посадкой Мони и эластичным поясом',
                      'Рубашка с блейзером свободного кроя',
                      'Брюки полуприлегающего силуэта песочного цвета'):
            with self.subTest(title=title):
                self.assertEqual(reliable_taste_features(product(title=title),
                    dimensions=('fit', 'silhouette')), {})

    def test_sleeveless_does_not_negate_colour_but_material_negation_still_blocks(self):
        value = product(title='Топ без рукавов в черном цвете', raw_params_json={'Цвет': 'Черный'})
        self.assertEqual(reliable_taste_features(value), {'color': {'black'}, 'sleeve': {'sleeveless'}})
        value = product(title='Топ без рукавов без шерсти', raw_params_json={'Материал': 'Шерсть'})
        self.assertNotIn('material', reliable_taste_features(value))
        value = product(title='Топ без рукавов не черного цвета', raw_params_json={'Цвет': 'Черный'})
        self.assertNotIn('color', reliable_taste_features(value))


if __name__ == '__main__':
    unittest.main()
