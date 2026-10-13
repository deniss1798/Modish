import unittest

from app.models import Product
from app.models import ProductSource
from app.services.catalog.merchant_adapters import get_adapter
from app.services.catalog.product_normalizer import normalized_to_dict
from app.services.catalog.size_resolver import resolve_size, resolve_product_sizes, exact_size_match


class SizeResolverTests(unittest.TestCase):
    def product(self, sizes, **fields):
        return Product(title='Брюки', category='брюки', source='shop', available_sizes=sizes, **fields)

    def test_preserves_raw_and_has_no_default_or_universal_conversion(self):
        for raw, system, value in [('S', 'LETTER', 'S'), ('М', 'LETTER', 'M'), ('2XL', 'LETTER', 'XXL'),
                ('EU 38', 'EU', '38'), ('RU:48', 'RU', '48'), ('W28', 'WAIST', 'W28')]:
            with self.subTest(raw=raw):
                size = resolve_size(raw, category='брюки', merchant_id='shop')
                self.assertEqual((size.raw_size, size.normalized_size, size.size_system), (raw, value, system))
                self.assertTrue(size.known)
                self.assertEqual(size.to_dict()['merchant_id'], 'shop')
        self.assertEqual(resolve_size(None).reason, 'size_unknown')
        self.assertEqual(resolve_size('48').reason, 'size_system_unknown')
        self.assertFalse(resolve_size('M/L').known)
        self.assertFalse(resolve_size('44-48', size_system='RU').known)

    def test_exact_numeric_and_cross_system_safety(self):
        product = self.product(['42', '44', '46'], feed_raw_json={'size_system': 'RU'})
        self.assertFalse(exact_size_match(product, '48', requested_system='RU'))
        self.assertTrue(exact_size_match(product, '44', requested_system='RU'))
        self.assertFalse(exact_size_match(product, '44', requested_system='EU'))
        self.assertFalse(exact_size_match(product, 'M', requested_system='LETTER'))

    def test_prefixed_sizes_and_waist_are_exact(self):
        product = self.product(['EU 38', 'W28L32'])
        self.assertTrue(exact_size_match(product, '38', requested_system='EU'))
        self.assertTrue(exact_size_match(product, 'W28L32'))
        self.assertFalse(exact_size_match(product, 'W28'))
        self.assertFalse(exact_size_match(product, '38', requested_system='RU'))
        self.assertFalse(resolve_size('EU38', size_system='RU').known)

    def test_conflicting_units_never_confirm_a_match(self):
        product = self.product(['M', 'EU 38'], feed_raw_json={'param_size_units': ['RU', 'EU']})
        self.assertFalse(exact_size_match(product, 'M'))
        self.assertFalse(exact_size_match(product, '38', requested_system='EU'))

    def test_per_size_stock_overrides_flat_available_list(self):
        product = self.product(['M', 'L'], available_sizes_detailed=[
            {'size': 'M', 'availability': 'out_of_stock'}, {'size': 'L', 'availability': 'available'}])
        self.assertFalse(exact_size_match(product, 'M'))
        self.assertTrue(exact_size_match(product, 'L'))
        sizes = resolve_product_sizes(product)
        self.assertEqual(sizes[0].availability, 'unavailable')
        self.assertEqual(sizes[0].raw_size, 'M')

    def test_legacy_inferred_numeric_system_is_not_evidence(self):
        product = self.product(['38'], size_system='EU')
        self.assertFalse(exact_size_match(product, '38', requested_system='EU'))

    def test_import_persists_original_size_alongside_resolved_contract(self):
        source = ProductSource(id='baon', code='baon', name='BAON', network='direct')
        raw = {'id': 'p', 'name': 'Женские брюки', 'category': 'брюки', 'price': '2000',
            'sizes': 'EU 38', 'available': True, 'gender': 'женский', 'currency': 'RUB'}
        normalized = get_adapter('baon').normalize(raw, source)
        payload = normalized_to_dict(normalized)
        size = payload['available_sizes_detailed'][0]
        self.assertEqual(size['raw_size'], 'EU 38')
        self.assertEqual(size['normalized_size'], '38')
        self.assertEqual(size['size_system'], 'EU')
        self.assertEqual(size['availability'], 'available')
        self.assertEqual(payload['feed_raw_json']['sizes'], 'EU 38')
        self.assertTrue(exact_size_match(Product(**payload), '38', requested_system='EU'))

    def test_quality_gate_understands_explicit_us_uk_without_converting(self):
        from app.services.catalog.quality_gate import recognized_size
        self.assertTrue(recognized_size('US 8', footwear=True))
        self.assertTrue(recognized_size('UK 6', footwear=True))
        self.assertTrue(recognized_size('2', size_system='US'))
        self.assertFalse(recognized_size('8', footwear=True))
        self.assertFalse(recognized_size('US 48', footwear=True))
        shoes = Product(title='Туфли', category='обувь', source='shop', available_sizes=['8'], feed_raw_json={'size_system': 'US'})
        self.assertTrue(exact_size_match(shoes, '8', requested_system='US'))
        self.assertFalse(exact_size_match(shoes, '8', requested_system='UK'))
