import unittest
from types import SimpleNamespace
from app.models import Product, ProductSource
from app.integrations.admitad.feed_parser import parse_yml_catalog_xml
from app.services.catalog.merchant_adapters import get_adapter
from app.services.catalog.product_normalizer import normalized_to_dict
from app.services.catalog.renormalize_service import renormalize_product_row
from app.services.catalog.size_evidence import explicit_size_system, confirmed_product_size
from app.services.feed_filters import product_has_confirmed_size
from app.services.fit_size_profile import update_size_profile


class SizeEvidenceTests(unittest.TestCase):
    def test_numeric_match_requires_both_systems_not_legacy_inference(self):
        p=Product(title='Брюки',category='брюки',available_sizes=['42'],size_system='EU')
        self.assertFalse(confirmed_product_size(p,'42',requested_system='EU'))
        p.feed_raw_json={'size_system':'RU'}
        self.assertTrue(confirmed_product_size(p,'42',requested_system='RU'))
        self.assertFalse(confirmed_product_size(p,'42',requested_system='EU'))
        self.assertFalse(confirmed_product_size(p,'42'))
        fit=SimpleNamespace(clothing_size='42',style_constraints={})
        self.assertFalse(product_has_confirmed_size(p,fit))
        fit.style_constraints={'clothing_size_system':'RU'}
        self.assertFalse(product_has_confirmed_size(p,fit))
        update_size_profile(fit, sizes={'bottom': {'value': '42', 'system': 'RU'}})
        self.assertTrue(product_has_confirmed_size(p,fit))

    def test_structured_evidence_handles_case_repeated_and_conflicting_units(self):
        self.assertEqual(explicit_size_system({'Size System':'ru'}),'RU')
        self.assertEqual(explicit_size_system(params={'Размер unit':'EU'}),'EU')
        self.assertIsNone(explicit_size_system({'size_system':'RU','param_size_unit':'EU'}))
        self.assertIsNone(explicit_size_system({'param_size_units':['EU','']}))
        self.assertIsNone(explicit_size_system({'size_system':123}))
        self.assertEqual(explicit_size_system({'param_size_units':['EU','EU']}),'EU')

    def test_yml_mixed_units_cannot_label_all_variants_with_last_system(self):
        for units,expected in [(['RU','EU'],None),(['RU',''],None),(['RU','RU'],'RU')]:
            params=''.join(f'<param name="Размер" unit="{unit}">42</param>' for unit in units)
            row=parse_yml_catalog_xml(f'<shop><offers><offer id="one">{params}</offer></offers></shop>')[0]
            self.assertEqual(explicit_size_system(row),expected)

    def test_compound_size_does_not_turn_into_two_confirmed_sizes(self):
        source=ProductSource(id='source',code='baon',name='BAON',network='direct')
        row={'id':'one','name':'Женская рубашка','price':'2000','currency':'RUB',
             'param_размер':'M/L','category':'рубашки','gender':'женский'}
        normalized=get_adapter('baon').normalize(row,source)
        self.assertEqual(normalized.sizes,['M/L'])
        p=Product(**normalized_to_dict(normalized))
        self.assertFalse(confirmed_product_size(p,'M'))
        self.assertFalse(confirmed_product_size(p,'L'))
        p.available_sizes=['M','L']
        self.assertFalse(confirmed_product_size(p,'M'))
        p.feed_raw_json={'sizes':'M|L'}
        self.assertTrue(confirmed_product_size(p,'M'))

    def test_renormalization_preserves_explicit_ru_and_removes_numeric_guess(self):
        for raw,expected in [({'param_размер_unit':'RU'},'RU'),({},None)]:
            p=Product(title='Женские брюки',category='брюки',available_sizes=['42'],
                size_system='EU',feed_raw_json=raw,colors=[])
            renormalize_product_row(p)
            self.assertEqual(p.size_system,expected)
