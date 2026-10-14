"""Closed alpha0.1: exact membership, never a largest-size or score fallback."""
from datetime import datetime, timezone
import unittest
from uuid import uuid4

from app.models import FitProfile, Product, TasteProfile
from app.services.mie_scoring import compute_fit_score, compute_mie_score
from app.services.catalog.size_resolver import exact_size_match
from app.services.feed_filters import product_has_confirmed_size
from app.services.product_eligibility import evaluate_eligibility
from fit_fixtures import confirmed_fit


class MIEExactSizeGateTests(unittest.TestCase):
    def fit(self, size='M', system=None):
        return confirmed_fit(clothing_size=size, gender_target='womenswear', budget_max=10000,
            height_cm=170, style_constraints={'clothing_size_system': system}, shoe_size='38', shoe_system='EU')

    def product(self, sizes=('M',), *, category='рубашки', raw=None, details=None):
        identifier = str(uuid4())
        return Product(id=identifier, external_id=identifier, title='Женская вещь', category=category,
            brand='Demo', source='test-shop', gender_target='womenswear', price=3000, currency='RUB',
            image_url='https://example.com/' + identifier + '.jpg', product_url='https://example.com/' + identifier,
            available_sizes=list(sizes) if isinstance(sizes, tuple) else sizes,
            feed_raw_json=raw or {}, available_sizes_detailed=details or [],
            is_active=1, is_available=1, is_deleted_from_feed=0, availability_status='in_stock',
            last_seen_in_feed_at=datetime.now(timezone.utc))

    def assert_gate(self, product, fit, expected):
        score, rejected, reasons, _ = compute_fit_score(product, fit=fit, palette=set(), avoid_colors=set())
        self.assertEqual(rejected, not expected)
        self.assertEqual('Есть ваш размер' in reasons, expected)
        self.assertEqual(product_has_confirmed_size(product, fit), expected)
        self.assertEqual(evaluate_eligibility(product, fit).eligible, expected)
        if not expected:
            self.assertEqual(score, 0.0)

    def test_letter_mismatch_is_hard_rejection_in_both_directions(self):
        for user, offered in (('S', ['XL']), ('L', ['M']), ('M', ['XS', 'XL']), ('XL', ['S', 'L'])):
            with self.subTest(user=user, offered=offered):
                self.assert_gate(self.product(offered), self.fit(user), False)

    def test_each_supported_letter_size_needs_literal_membership(self):
        letters = ('XS', 'S', 'M', 'L', 'XL', 'XXL')
        for user in letters:
            for offered in letters:
                with self.subTest(user=user, offered=offered):
                    self.assert_gate(self.product([offered]), self.fit(user), user == offered)

    def test_numeric_match_needs_exact_value_and_same_system(self):
        fit = self.fit('46', 'RU')
        for offered, system, expected in ((['48'], 'RU', False), (['44', '48'], 'RU', False),
                                       (['46'], 'EU', False), (['46'], None, False),
                                       (['44', '46', '48'], 'RU', True)):
            with self.subTest(offered=offered, system=system):
                self.assert_gate(self.product(offered, raw={'size_system': system}), fit, expected)

    def test_missing_fit_or_unconfirmed_legacy_m_never_scores_as_a_fit(self):
        for fit in (None, FitProfile(clothing_size='M', gender_target='womenswear', budget_max=10000)):
            with self.subTest(fit=fit):
                self.assert_gate(self.product(['M']), fit, False)

    def test_single_scalar_size_is_one_token_not_characters(self):
        for offered, user, expected in (('XL', 'L', False), ('XS', 'S', False), ('XXL', 'XL', False),
                                       ('XL', 'XL', True), ('M', 'M', True)):
            with self.subTest(offered=offered, user=user):
                self.assert_gate(self.product(offered), self.fit(user), expected)

    def test_mapping_keys_do_not_establish_available_sizes(self):
        product = self.product({'M': 'legacy_unknown'})
        self.assertFalse(exact_size_match(product, 'M', requested_system='LETTER'))
        self.assertFalse(product_has_confirmed_size(product, self.fit()))

    def test_requested_size_unavailable_or_unknown_is_not_confirmed_by_flat_list(self):
        for state in ('unavailable', 'out_of_stock', 'unknown'):
            with self.subTest(state=state):
                self.assert_gate(self.product(['M', 'L'], details=[{'size': 'M', 'availability': state}]), self.fit(), False)

    def test_shoes_use_the_confirmed_shoe_slot_and_system(self):
        fit = self.fit('L')
        for sizes, system, expected in ((['38'], 'EU', True), (['39'], 'EU', False),
                                        (['38'], 'RU', False), (['38'], None, False), (['L'], None, False)):
            with self.subTest(sizes=sizes, system=system):
                self.assert_gate(self.product(sizes, category='обувь', raw={'size_system': system}), fit, expected)

    def test_compound_size_does_not_confirm_split_endpoints(self):
        for offered, raw, user, system in ((['S', 'L'], {'sizes': 'S/L'}, 'S', None),
                (['44', '48'], {'sizes': '44-48', 'size_system': 'RU'}, '44', 'RU')):
            self.assert_gate(self.product(offered, raw=raw), self.fit(user, system), False)

    def test_strong_taste_and_popularity_cannot_rescue_missing_exact_size(self):
        product = self.product(['XL'])
        score = compute_mie_score(product, taste=TasteProfile(), fit=self.fit('S'), palette={'black'},
            avoid_colors=set(), taste_features={('item', product.id): (1.0, 1.0)}, popularity=100000)
        self.assertTrue(score.hard_reject)
        self.assertEqual(score.final_score, 0.0)
        self.assertNotIn('Есть ваш размер', score.reasons)

    def test_malformed_profile_or_stock_evidence_cannot_be_claimed_in_explanations(self):
        from app.services import activation_service as activation
        from app.services.intent_explanations import product_reasons
        from app.services.shopping_intent_parser import parse_intent
        from app.services.personal_size_gate import evaluate_personal_size
        spec = parse_intent('Рубашка до 5000')
        product = self.product(['M'])
        for profile in ({'sizes': []}, {'sizes': {'top': {'state': 'confirmed', 'value': ['M'], 'system': 'LETTER'}}},
                        {'sizes': {'top': {'state': 'confirmed', 'value': 'M', 'system': None}}}):
            with self.subTest(profile=profile):
                fit = self.fit()
                fit.size_profile_json = profile
                self.assertFalse(evaluate_personal_size(product, fit).eligible)
                self.assertFalse(activation.confirmed_size(product, fit, None))
                self.assertIsNone(activation._matched_confirmed_size(product, fit))
                self.assertEqual(product_reasons(product, fit, spec, facts={}), [])
        product.available_sizes_detailed = {'size': 'M', 'availability': 'unavailable'}
        self.assert_gate(product, self.fit(), False)


if __name__ == '__main__':
    unittest.main()
