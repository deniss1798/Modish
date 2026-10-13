"""Cheap impossible-size rejection preserves the complete evidence contract."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.models import Product
from app.services.catalog import size_resolver as sizes


def full_size_match(product, requested, *, requested_system=None):
    """The original exact matcher, with no flat-token admission shortcut."""
    if not requested:
        return False
    wanted = sizes.resolve_size(requested, size_system=requested_system,
        category=product.category or product.category_name or '')
    if not wanted.known:
        return False
    return any(size.known and size.availability == 'available'
        and size.size_system == wanted.size_system and size.normalized_size == wanted.normalized_size
        for size in sizes.resolve_product_sizes(product))


class UnreadSizeEvidence:
    category = 'брюки'
    category_name = ''

    def __init__(self, flat_sizes):
        self.available_sizes = flat_sizes

    @property
    def feed_raw_json(self):
        raise AssertionError('Impossible flat sizes must not parse the raw merchant row')

    @property
    def raw_params_json(self):
        raise AssertionError('Impossible flat sizes must not parse raw parameters')

    @property
    def available_sizes_detailed(self):
        raise AssertionError('Impossible flat sizes must not inspect detailed stock')


class SizeAdmissionPerformanceTests(unittest.TestCase):
    def product(self, flat_sizes, *, category='брюки', **fields):
        return Product(title='Товар', category=category, source='size-admission-test',
            available_sizes=flat_sizes, **fields)

    def test_impossible_flat_sizes_do_not_read_raw_parameters_or_detailed_stock(self):
        cases = [(['s', 'М', 'EU 38', 'W30L32', 'L/XL'], 'L', 'LETTER'),
            (['RU 37', 'eu39', '38,5', '38/39'], '38', 'EU'),
            (['W30', 'W30L30', 'WAIST:W30L32'], 'W30L32', 'WAIST'),
            (['7', '5', '7,5,8,5', '7.50'], '7.5', 'UK'),
            ([], 'M', 'LETTER'), (None, 'M', 'LETTER')]
        with patch.object(sizes, 'resolve_product_sizes', side_effect=AssertionError('Full resolution is unnecessary')):
            for flat, requested, system in cases:
                with self.subTest(flat=flat, requested=requested):
                    self.assertFalse(sizes.exact_size_match(UnreadSizeEvidence(flat), requested,
                        requested_system=system))

    def test_token_pairs_across_confirmed_profile_categories_match_full_reference(self):
        tokens = [None, '', 0, 38, '0', '7', '7,5', '7, 5', '7.50', '38', 'eu38', 'EU:38',
            'RU-38', 'US8', 'UK8', 'UK:7,5', 'INT:M', 'LETTER:2XL', 'INT:3XS', 'LETTERМ',
            'm', 'М', 'хl', '2ХL', '2XL', '3XS', '4XL', '6XS', 'XXXS', 'W30', 'w30l32',
            'W30L30', 'W30 L32', 'WAIST:W30', 'M/L', '44-48', '7,5,8,5', 'UKUK7.5',
            'INTM/L', 'not-a-size', ['M'], {'size': 'M'}, True, 38.5]
        requests = [('M', 'LETTER'), ('М', 'INT'), ('2XL', 'LETTER'), ('LETTER:2XL', 'LETTER'),
            ('3XS', 'INT'), ('INT:3XS', 'INT'), ('38', 'EU'), ('EU38', None), ('38', 'RU'),
            ('8', 'UK'), ('8', 'US'), ('7.5', 'UK'), ('7,5', 'US'), ('0', 'EU'),
            ('W30L32', 'WAIST'), ('W30', None), ('M/L', 'LETTER'), ('38', None)]
        evidence = [{}, *[{'size_system': system} for system in ('LETTER', 'INT', 'EU', 'RU', 'UK', 'US', 'WAIST')],
            {'param_size_units': ['EU', 'RU']}]
        for category in ('рубашки', 'брюки', 'платья', 'обувь'):
            for raw in evidence:
                for token in tokens:
                    product = self.product([token], category=category, feed_raw_json=raw)
                    for requested, system in requests:
                        with self.subTest(category=category, raw=raw, token=token, requested=requested, system=system):
                            self.assertEqual(sizes.exact_size_match(product, requested, requested_system=system),
                                full_size_match(product, requested, requested_system=system))

    def test_possible_matches_still_resolve_units_original_compounds_and_stock(self):
        cases = [(['EU38'], {'feed_raw_json': {'size_system': 'RU'}}, '38', 'EU'),
            (['M'], {'feed_raw_json': {'param_size_units': ['LETTER', 'EU']}}, 'M', 'LETTER'),
            (['38'], {'feed_raw_json': {'size_system': 'EU'}, 'raw_params_json': {'size_unit': 'RU'}}, '38', 'EU'),
            (['38'], {'feed_raw_json': {'size_system': 'EU'}, 'available_sizes_detailed': [
                {'raw_size': '38', 'availability': 'out_of_stock'}]}, '38', 'EU'),
            (['38'], {'feed_raw_json': {'size_system': 'EU'}, 'available_sizes_detailed': [
                {'size': '38', 'availability': 'available'}, {'size': '38', 'availability': 'unknown'}]}, '38', 'EU'),
            (['7.5'], {'feed_raw_json': {'sizes': ['7, 5'], 'size_system': 'UK'},
                'available_sizes_detailed': [{'raw_size': '7,5', 'availability': 'unavailable'}]}, '7.5', 'UK'),
            (['M', 'L'], {'feed_raw_json': {'sizes': 'M/L'}}, 'M', 'LETTER'),
            (['44', '48'], {'feed_raw_json': {'sizes': '44-48', 'size_system': 'RU'}}, '44', 'RU'),
            (['7', '5'], {'feed_raw_json': {'sizes': '7,5', 'size_system': 'UK'}}, '7', 'UK'),
            (['38'], {'size_system': 'EU'}, '38', 'EU')]
        for flat, fields, requested, system in cases:
            with self.subTest(flat=flat, fields=fields, requested=requested):
                product = self.product(flat, **fields)
                self.assertFalse(full_size_match(product, requested, requested_system=system))
                with patch.object(sizes, 'resolve_product_sizes', wraps=sizes.resolve_product_sizes) as resolver:
                    self.assertFalse(sizes.exact_size_match(product, requested, requested_system=system))
                resolver.assert_called_once_with(product)

    def test_original_aliases_and_falsey_numeric_values_keep_positive_matches(self):
        cases = [(['M'], {'sizes': ['М']}, 'M', 'LETTER'),
            (['XXL'], {'sizes': ['2ХL']}, '2XL', 'LETTER'),
            (['7.5'], {'sizes': ['7, 5'], 'size_system': 'UK'}, '7,5', 'UK'),
            (['EU38'], {'sizes': ['eu 38']}, '38', 'EU'),
            (['LETTER:2XL'], {'sizes': ['letter:2xl']}, 'LETTER:2XL', 'LETTER'),
            ([0], {'sizes': [0], 'size_system': 'EU'}, '0', 'EU')]
        for flat, raw, requested, system in cases:
            with self.subTest(flat=flat, raw=raw):
                product = self.product(flat, feed_raw_json=raw)
                self.assertTrue(full_size_match(product, requested, requested_system=system))
                self.assertTrue(sizes.exact_size_match(product, requested, requested_system=system))

    def test_encoded_letter_alias_is_not_renormalized_after_prefix_removal(self):
        prefixed = self.product(['LETTER:2XL'])
        plain = self.product(['2XL'])
        self.assertTrue(sizes.exact_size_match(prefixed, 'LETTER:2XL', requested_system='LETTER'))
        self.assertFalse(sizes.exact_size_match(prefixed, '2XL', requested_system='LETTER'))
        self.assertTrue(sizes.exact_size_match(plain, '2XL', requested_system='LETTER'))
        self.assertFalse(sizes.exact_size_match(plain, 'LETTER:2XL', requested_system='LETTER'))

    def test_late_exact_token_after_more_than_one_thousand_invalid_values_is_admitted(self):
        product = self.product(['M/L'] * 1201 + ['eu 38'], category='обувь',
            feed_raw_json={'sizes': ['M/L', 'eu 38']},
            available_sizes_detailed=[{'raw_size': 'EU38', 'availability': 'in_stock'}])
        self.assertTrue(full_size_match(product, '38', requested_system='EU'))
        with patch.object(sizes, 'resolve_product_sizes', wraps=sizes.resolve_product_sizes) as resolver:
            self.assertTrue(sizes.exact_size_match(product, '38', requested_system='EU'))
        resolver.assert_called_once_with(product)

    def test_read_probe_preserves_aliased_evidence_and_sees_subsequent_changes(self):
        flat = ['S']
        raw = {'sizes': ['S'], 'metadata': {'units': ['merchant-specific']}}
        details = [{'size': 'S', 'availability': 'available'}]
        product = self.product(flat, feed_raw_json=raw, available_sizes_detailed=details)
        aliased = self.product(flat, feed_raw_json=raw, available_sizes_detailed=details)
        before = deepcopy((flat, raw, details))
        for _ in range(3):
            self.assertFalse(sizes.exact_size_match(product, 'M', requested_system='LETTER'))
            self.assertTrue(sizes.exact_size_match(aliased, 'S', requested_system='LETTER'))
        self.assertEqual((flat, raw, details), before)
        self.assertIs(product.available_sizes, aliased.available_sizes)
        self.assertIs(product.feed_raw_json, raw)
        self.assertIs(product.available_sizes_detailed, details)
        flat[0] = 'M'; raw['sizes'][0] = 'M'; details[0]['size'] = 'M'
        self.assertTrue(sizes.exact_size_match(product, 'M', requested_system='LETTER'))
        self.assertFalse(sizes.exact_size_match(aliased, 'S', requested_system='LETTER'))

    def test_legacy_iterators_and_malformed_reusable_containers_keep_full_behavior(self):
        for factory in (lambda: iter(['S', 'M']), lambda: {'M': 'legacy'}, lambda: 'M', lambda: ('S', 'M')):
            reference = SimpleNamespace(category='брюки', category_name='', source='legacy',
                available_sizes=factory())
            product = SimpleNamespace(category='брюки', category_name='', source='legacy',
                available_sizes=factory())
            self.assertTrue(full_size_match(reference, 'M', requested_system='LETTER'))
            with patch.object(sizes, 'resolve_product_sizes', wraps=sizes.resolve_product_sizes) as resolver:
                self.assertTrue(sizes.exact_size_match(product, 'M', requested_system='LETTER'))
            resolver.assert_called_once_with(product)


if __name__ == '__main__':
    unittest.main()
