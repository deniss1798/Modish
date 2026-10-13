"""Activation explanations follow exact current Fit slots, never legacy sizes."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from app.models import Outfit
from app.services import activation_service as activation
from app.services.catalog.size_resolver import exact_size_match
from app.services.fit_size_profile import requested_size_for_product, update_size_profile
from app.services.garment_roles import product_role
import test_activation as fixtures


class ActivationSizeReasonTests(unittest.TestCase):
    product = fixtures.ActivationTests.product
    seed = fixtures.ActivationTests.seed
    learn = fixtures.ActivationTests.learn
    tearDown = fixtures.ActivationTests.tearDown

    def setUp(self):
        fixtures.ActivationTests.setUp(self)
        update_size_profile(self.fit, sizes={
            'top': {'value': '48', 'system': 'RU'},
            'bottom': {'value': '50', 'system': 'RU'},
            'one_piece': {'value': '46', 'system': 'RU'},
            'shoes': {'value': '39', 'system': 'EU'}})
        self.fit.clothing_size = '46'
        self.session.shoe_size = '38'
        self.profile, _, self.taste = activation._preferences(self.db, self.user)
        self.db.flush()

    def reasons(self, product, *, fit=None):
        return activation.reasons_for(product, self.profile,
            self.fit if fit is None else fit, self.taste, self.session)

    def slot_product(self, category, value, system, **changes):
        return self.product(category, available_sizes=[value], feed_raw_json={'size_system': system}, **changes)

    def test_each_garment_reason_uses_its_actual_confirmed_slot_value_and_system(self):
        for category, value, system in [('рубашки', '48', 'RU'), ('брюки', '50', 'RU'),
                ('платья', '46', 'RU'), ('обувь', '39', 'EU')]:
            with self.subTest(category=category):
                product = self.slot_product(category, value, system)
                self.assertTrue(activation.product_ok(product, self.fit, self.session))
                self.assertIn(f'Есть размер {value} {system}', ' '.join(self.reasons(product)))
                matched = activation._matched_confirmed_size(product, self.fit)
                self.assertEqual((matched.normalized_size, matched.size_system, matched.availability),
                    (value, system, 'available'))
        self.assertEqual((self.fit.clothing_size, self.session.shoe_size), ('46', '38'))

    def test_outerwear_letter_waist_and_encoded_half_sizes_keep_exact_system_evidence(self):
        coat = self.slot_product('рубашки', '48', 'RU', title='Пальто')
        coat.category = 'верхний_слой'
        self.assertEqual(product_role(coat), 'outerwear')
        self.assertIn('Есть размер 48 RU', ' '.join(self.reasons(coat)))
        for slot, category, requested, system, merchant in [
                ('top', 'рубашки', 'M', 'LETTER', 'М'),
                ('bottom', 'брюки', 'W30L32', 'WAIST', 'w30l32'),
                ('shoes', 'обувь', '7.5', 'UK', 'UK:7,5')]:
            with self.subTest(slot=slot):
                update_size_profile(self.fit, sizes={slot: {'value': requested, 'system': system}})
                product = self.slot_product(category, merchant, system)
                self.assertIn(f'Есть размер {requested} {system}', ' '.join(self.reasons(product)))

    def test_unknown_unconfirmed_and_unsupported_direct_calls_never_claim_a_size(self):
        product = self.slot_product('брюки', '46', 'RU')
        self.assertNotIn('Есть размер', ' '.join(self.reasons(product)), 'Legacy46 cannot replace confirmed bottom50')
        update_size_profile(self.fit, sizes={'bottom': None})
        product.available_sizes = ['50']
        self.assertNotIn('Есть размер', ' '.join(self.reasons(product)))
        unsupported = self.slot_product('рубашки', '48', 'RU', title='Ремень кожаный')
        unsupported.category = 'аксессуары'
        self.assertIsNone(activation._matched_confirmed_size(unsupported, self.fit))
        self.assertNotIn('Есть размер', ' '.join(self.reasons(unsupported)))
        self.assertEqual(activation.reasons_for(product, None, None, None, None), [])
        self.assertIsNone(activation._matched_confirmed_size(None, self.fit))

    def test_conflicting_unknown_systems_and_per_size_stock_suppress_presence_claims(self):
        product = self.slot_product('обувь', '39', 'EU')
        cases = [({'available_sizes': ['38']}, False),
            ({'feed_raw_json': {'size_system': 'RU'}}, False),
            ({'feed_raw_json': {}}, False),
            ({'feed_raw_json': {}, 'size_system': 'EU'}, False),
            ({'feed_raw_json': {'param_size_units': ['EU', 'RU']}}, False),
            ({'available_sizes_detailed': [{'size': '39', 'availability': 'unknown'}]}, False),
            ({'available_sizes_detailed': [{'size': '39', 'availability': 'out_of_stock'}]}, False),
            ({'available_sizes': ['EU39'], 'feed_raw_json': {}}, True)]
        baseline = {key: deepcopy(getattr(product, key)) for key in (
            'available_sizes', 'feed_raw_json', 'size_system', 'available_sizes_detailed')}
        for changes, expected in cases:
            with self.subTest(changes=changes):
                for key, value in baseline.items():
                    setattr(product, key, deepcopy(value))
                for key, value in changes.items():
                    setattr(product, key, value)
                requested, system = requested_size_for_product(self.fit, product)
                self.assertEqual(exact_size_match(product, requested, requested_system=system), expected)
                self.assertEqual(activation._matched_confirmed_size(product, self.fit) is not None, expected)
                self.assertEqual('Есть размер 39 EU' in ' '.join(self.reasons(product)), expected)

    def test_explanations_read_current_slots_without_mutating_evidence_or_using_general_size(self):
        product = self.slot_product('брюки', '50', 'RU')
        product.available_sizes = ['50', '52']
        before = deepcopy((self.fit.size_profile_json, product.feed_raw_json, product.available_sizes))
        self.assertIn('Есть размер 50 RU', ' '.join(self.reasons(product)))
        self.assertEqual(before, (self.fit.size_profile_json, product.feed_raw_json, product.available_sizes))
        update_size_profile(self.fit, sizes={'bottom': {'value': '52', 'system': 'RU'}})
        self.assertIn('Есть размер 52 RU', ' '.join(self.reasons(product)))
        self.assertNotIn('Есть размер 50 RU', ' '.join(self.reasons(product)))
        self.assertEqual(self.fit.clothing_size, '46')
        product.price = self.fit.budget_max + 1
        self.assertNotIn('в бюджете', ' '.join(self.reasons(product)))

    def test_direct_stale_or_globally_unavailable_product_cannot_claim_its_exact_size(self):
        product = self.slot_product('обувь', '39', 'EU')
        baseline = {key: deepcopy(getattr(product, key)) for key in (
            'is_available', 'is_active', 'availability_status', 'is_deleted_from_feed', 'last_seen_in_feed_at')}
        cases = [{'is_available': 0}, {'is_active': 0}, {'is_deleted_from_feed': 1},
            {'availability_status': 'out_of_stock'}, {'availability_status': 'unknown'},
            {'last_seen_in_feed_at': datetime.now(timezone.utc)-timedelta(hours=73)}]
        for changes in cases:
            with self.subTest(changes=changes):
                for key, value in baseline.items():
                    setattr(product, key, deepcopy(value))
                for key, value in changes.items():
                    setattr(product, key, value)
                self.assertTrue(exact_size_match(product, '39', requested_system='EU'))
                self.assertIsNone(activation._matched_confirmed_size(product, self.fit))
                self.assertNotIn('Есть размер', ' '.join(self.reasons(product)))
                self.assertNotIn('Размеры указаны в наличии', activation._wow_explanation([product], self.profile, self.fit))

    def test_new_wow_explanation_has_confirmed_slot_evidence_without_legacy_numbers(self):
        products = self.seed()
        values = {'top': ('48', 'RU'), 'bottom': ('50', 'RU'),
            'one_piece': ('46', 'RU'), 'shoes': ('39', 'EU')}
        for product in products:
            value, system = values[product_role(product)]
            product.available_sizes = [value]
            product.feed_raw_json = {'size_system': system}
        self.db.flush()
        self.learn()
        result = activation.complete_wow(self.db, self.user, self.session)
        self.assertTrue(result['outfits'])
        for row in result['outfits']:
            self.assertIn('Размеры указаны в наличии', row['explanation'])
            self.assertNotIn('размеры 46 и 38', row['explanation'])
            for pid in row['items'].values():
                matched = activation._matched_confirmed_size(self.db.get(fixtures.Product, pid), self.fit)
                self.assertIsNotNone(matched)
        self.assertTrue(all('Размеры указаны в наличии' in row['explanation'] for row in self.session.wow_json))

    def test_cached_wow_rebuilds_current_explanation_without_reranking_or_mutating_snapshot(self):
        top = self.slot_product('рубашки', '48', 'RU')
        bottom = self.slot_product('брюки', '50', 'RU')
        shoe = self.slot_product('обувь', '39', 'EU')
        shoe.available_sizes = ['39', '40']
        row = Outfit(id='cached-size-wow', user_id=self.user.id,
            items_json={'top': top.id, 'bottom': bottom.id, 'shoes': shoe.id},
            total_price=4500, style_direction='daily', reason='Старое пояснение', score=.8)
        self.db.add(row)
        self.session.swipes_json = {str(index): 'like' for index in range(15)}
        self.session.completed_at = datetime.now(timezone.utc)
        self.session.wow_json = [{'id': row.id, 'title': 'На каждый день',
            'explanation': 'Повседневный стиль · до 25000 ₽ · размеры 46 и 38'}]
        self.fit.budget_max = 6000
        update_size_profile(self.fit, sizes={'shoes': {'value': '40', 'system': 'EU'}})
        self.db.flush()
        snapshot = deepcopy(self.session.wow_json)
        with patch('app.services.outfit_engine_v2.generate_outfits_v2', side_effect=AssertionError('Cached completion cannot rebuild')):
            result = activation.complete_wow(self.db, self.user, self.session)
        self.assertEqual([item['id'] for item in result['outfits']], [row.id])
        text = result['outfits'][0]['explanation']
        self.assertIn('до 6000 ₽', text)
        self.assertIn('Размеры указаны в наличии', text)
        self.assertNotIn('25000', text)
        self.assertNotIn('46 и 38', text)
        self.assertEqual(activation._matched_confirmed_size(shoe, self.fit).normalized_size, '40')
        self.assertEqual(self.session.wow_json, snapshot)
        update_size_profile(self.fit, sizes={'shoes': {'value': '41', 'system': 'EU'}})
        self.db.flush()
        with patch.object(activation, '_wow_explanation', side_effect=AssertionError('An ineligible outfit cannot receive an availability claim')):
            self.assertEqual(activation.complete_wow(self.db, self.user, self.session)['outfits'], [])
        self.assertEqual(self.session.wow_json, snapshot)

    def test_direct_wow_explanation_does_not_assert_unknown_slots_or_invalid_budget(self):
        self.assertNotIn('Размеры указаны в наличии', activation._wow_explanation([], self.profile, self.fit))
        self.assertNotIn('Размеры указаны в наличии', activation._wow_explanation([None], self.profile, self.fit))
        wrong_size = self.slot_product('брюки', '46', 'RU')
        wrong_size.price = self.fit.budget_max + 1
        text = activation._wow_explanation([wrong_size], self.profile, self.fit)
        self.assertNotIn('Размеры указаны в наличии', text)
        self.assertNotIn('до ', text)


if __name__ == '__main__':
    unittest.main()
