"""Requested-size admission agrees with complete evidence resolution."""
from copy import deepcopy
from itertools import product as combinations
from types import SimpleNamespace
import unittest

from app.services.catalog.size_resolver import (
    exact_size_match, resolve_product_sizes, resolve_size,
)


def item(**changes):
    fields = dict(available_sizes=['XS', 'S', 'M', 'L', 'XL'],
        available_sizes_detailed=[], feed_raw_json={'size_system': 'LETTER'},
        raw_params_json={}, category='рубашки', category_name='',
        source='baon', source_id='test-merchant')
    fields.update(changes)
    return SimpleNamespace(**fields)


def complete_match(row, requested, system):
    wanted = resolve_size(requested, size_system=system,
        category=row.category or row.category_name or '')
    return wanted.known and any(size.known and size.availability == 'available'
        and size.size_system == wanted.size_system
        and size.normalized_size == wanted.normalized_size
        for size in resolve_product_sizes(row))


class TargetedSizeResolutionTests(unittest.TestCase):
    def assert_complete_agreement(self, row, requested, system):
        expected = complete_match(deepcopy(row), requested, system)
        self.assertEqual(exact_size_match(row, requested, requested_system=system), expected)

    def test_complete_evidence_equivalence_across_units_originals_and_stock(self):
        flat_options = [None, [], ['XS', 'M', 'L', 'XL'], ['EU:37', 'EU:38', 'EU:39'],
            ['RU-38', '38', 'EU38'], ['М', 'ХL', '2XL', '3XL'],
            ['7,5', '8'], ['M/L', 'L'], ['W28', 'W30L32'],
            ['L', 'LETTER:L', 'INT:L'], ['L', 'L'], 'XL', {'L': True}, b'L']
        raw_options = [{}, {'size_system': 'LETTER'}, {'size_system': 'EU'},
            {'size_system': ['EU', 'RU']}, {'sizes': 'M/L', 'size_system': 'LETTER'},
            {'sizes': '7,5;8', 'size_system': 'UK'},
            {'sizes': ['L', 'XL'], 'size_system': 'INT'}]
        details_options = [[], [{'raw_size': 'L', 'availability': 'available'}],
            [{'raw_size': 'L', 'availability': 'out_of_stock'}],
            [{'raw_size': 'L', 'availability': 'available'},
             {'size': 'L', 'availability': 'unknown'}],
            [{'size': '38', 'availability': 'in_stock'}], {'L': 'available'}, 'available']
        requests = [('L', 'LETTER'), ('XL', 'INT'), ('38', 'EU'),
            ('38', 'RU'), ('7.5', 'UK'), ('W28', 'WAIST'), ('', None)]
        for flat, raw, details, (requested, system) in combinations(
                flat_options, raw_options, details_options, requests):
            with self.subTest(flat=flat, raw=raw, details=details,
                              requested=requested, system=system):
                self.assert_complete_agreement(item(available_sizes=deepcopy(flat),
                    feed_raw_json=deepcopy(raw), available_sizes_detailed=deepcopy(details)),
                    requested, system)

    def test_live_flat_original_system_and_stock_edits_change_admission(self):
        row = item()
        self.assertTrue(exact_size_match(row, 'L', requested_system='LETTER'))
        row.available_sizes.remove('L')
        self.assertFalse(exact_size_match(row, 'L', requested_system='LETTER'))
        row.available_sizes.append('L')
        self.assertTrue(exact_size_match(row, 'L', requested_system='LETTER'))
        row.feed_raw_json['sizes'] = 'M/L'
        self.assertFalse(exact_size_match(row, 'L', requested_system='LETTER'))
        row.feed_raw_json['sizes'] = ['L', 'XL']
        self.assertTrue(exact_size_match(row, 'L', requested_system='LETTER'))
        row.raw_params_json['size_system'] = 'EU'
        self.assertFalse(exact_size_match(row, 'L', requested_system='LETTER'))
        row.raw_params_json.clear()
        self.assertTrue(exact_size_match(row, 'L', requested_system='LETTER'))
        row.available_sizes_detailed.append({'raw_size': 'L', 'availability': 'unavailable'})
        self.assertFalse(exact_size_match(row, 'L', requested_system='LETTER'))
        row.available_sizes_detailed[0]['availability'] = 'available'
        self.assertTrue(exact_size_match(row, 'L', requested_system='LETTER'))
        row.available_sizes_detailed.append({'raw_size': 'L', 'availability': 'unknown'})
        self.assertFalse(exact_size_match(row, 'L', requested_system='LETTER'))
        row.available_sizes_detailed.pop()
        self.assertTrue(exact_size_match(row, 'L', requested_system='LETTER'))

    def test_category_and_explicit_prefix_still_control_exact_units(self):
        row = item(available_sizes=['W28', 'W30L32'],
            feed_raw_json={'size_system': 'WAIST'}, category='брюки')
        self.assertTrue(exact_size_match(row, 'W28', requested_system='WAIST'))
        row.category = 'рубашки'
        self.assertFalse(exact_size_match(row, 'W28', requested_system='WAIST'))
        row.category = 'обувь'
        row.available_sizes = ['EU37', 'EU:38', 'EU-39']
        row.feed_raw_json = {}
        self.assertTrue(exact_size_match(row, '38', requested_system='EU'))
        self.assertFalse(exact_size_match(row, '38', requested_system='RU'))
        row.feed_raw_json['size_system'] = 'RU'
        self.assertFalse(exact_size_match(row, '38', requested_system='EU'))

    def test_legacy_iterators_preserve_full_resolution_consumption(self):
        # Full resolution consumes a stock-evidence iterator at the first flat
        # size. It must not move that evidence to the requested later size.
        row = item(available_sizes=['M', 'L'], available_sizes_detailed=iter([
            {'raw_size': 'L', 'availability': 'unavailable'}]))
        self.assertTrue(exact_size_match(row, 'L', requested_system='LETTER'))
        self.assertEqual(list(row.available_sizes_detailed), [])
        row = item(available_sizes=iter(['M', 'L', 'XL']))
        self.assertTrue(exact_size_match(row, 'L', requested_system='LETTER'))
        self.assertEqual(list(row.available_sizes), [])


if __name__ == '__main__':
    unittest.main()
