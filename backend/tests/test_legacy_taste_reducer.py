"""Independent business examples for the detached legacy Taste projection."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
import unittest

from app.services.legacy_taste_reducer import (
    LegacyProductFacts, legacy_product_facts, reduce_legacy_taste,
)
from app.services.recommendation_config import VALID_DISLIKE_REASONS


LIST_FIELDS = (
    'liked_categories', 'disliked_categories', 'liked_brands', 'disliked_brands',
    'liked_colors', 'disliked_colors', 'liked_styles', 'disliked_styles',
)
WEIGHT_FIELDS = ('category_weights', 'brand_weights', 'color_weights', 'style_weights')


def empty_projection(**changes):
    values = {name: [] for name in LIST_FIELDS}
    values.update({name: {} for name in WEIGHT_FIELDS})
    values.update(deepcopy(changes))
    return values


def sample_facts():
    return LegacyProductFacts(categories=('рубашки', 'футболки'), category='рубашки',
        brand='baon', colors=('black', 'white', 'navy', 'pink'),
        styles=('casual', 'minimalism', 'business', 'sport'))


def weights(delta, *, category=False, brand=False, color=False, style=False):
    """Expected fixture values; do not call the production target/weight policy."""
    return empty_projection(
        category_weights={'рубашки': delta, 'футболки': delta} if category else {},
        brand_weights={'baon': delta} if brand else {},
        color_weights={'black': delta, 'white': delta, 'navy': delta} if color else {},
        style_weights={'casual': delta, 'minimalism': delta, 'business': delta} if style else {})


class LegacyTasteReducerTests(unittest.TestCase):
    def taste(self, **changes):
        return SimpleNamespace(**empty_projection(**changes))

    def reduce(self, event_type, *, meta=None, taste=None, facts=None, weight=None):
        return reduce_legacy_taste(taste if taste is not None else self.taste(),
            facts if facts is not None else sample_facts(), event_type=event_type,
            meta={} if meta is None else meta, weight=weight)

    def test_each_event_has_independent_numeric_expectations_without_lists(self):
        cases = (
            ('impression', empty_projection()),
            ('view', weights(.1, category=True, style=True)),
            ('skip', weights(-.5, category=True, style=True)),
            ('dislike', weights(-.75, category=True, style=True)),
            ('like', weights(2, category=True, brand=True, color=True, style=True)),
            ('save', weights(4, category=True, brand=True, color=True, style=True)),
            ('unsave', weights(-2, category=True, brand=True, color=True, style=True)),
            ('open_product', weights(1, category=True, brand=True, color=True, style=True)),
            ('affiliate_click', weights(5, category=True, brand=True, color=True, style=True)),
            ('purchase', weights(10, category=True, brand=True, color=True, style=True)),
            ('post_purchase_positive', weights(15, category=True, brand=True, color=True, style=True)),
            ('post_purchase_negative', weights(-2.5, category=True, style=True)),
        )
        for event_type, expected in cases:
            with self.subTest(event_type=event_type):
                self.assertEqual(self.reduce(event_type), expected)

    def test_each_configured_dislike_reason_targets_only_its_intended_dimensions(self):
        expected = {
            'not_my_style': weights(-3, category=True, style=True),
            'dont_like_color': weights(-3, color=True),
            'dont_like_fit': empty_projection(),
            'dont_like_category': weights(-3, category=True),
            'dont_like_brand': weights(-3, brand=True),
            'too_expensive': empty_projection(),
            'already_have_similar': empty_projection(),
            'dont_like_design': weights(-3, category=True, style=True),
            'not_for_context': empty_projection(),
            'other': weights(-3, category=True, style=True),
        }
        self.assertEqual(set(expected), set(VALID_DISLIKE_REASONS), 'Every configured reason needs a business fixture.')
        for reason, projection in expected.items():
            with self.subTest(reason=reason):
                self.assertEqual(self.reduce('dislike', meta={'reason': reason}), projection)

    def test_omitted_null_and_blank_reason_are_soft_but_explicit_other_is_strong(self):
        for meta in ({}, {'reason': None}, {'reason': ''}, {'reason': '  '}):
            with self.subTest(meta=meta):
                self.assertEqual(self.reduce('dislike', meta=meta), weights(-.75, category=True, style=True))
        self.assertEqual(self.reduce('dislike', meta={'reason': 'other'}),
            weights(-3, category=True, style=True))

    def test_post_purchase_negative_keeps_reason_targeting_and_its_own_weight(self):
        self.assertEqual(self.reduce('post_purchase_negative', meta={'reason': 'dont_like_color'}),
            weights(-10, color=True))
        self.assertEqual(self.reduce('post_purchase_negative', meta={'reason': 'not_my_style'}),
            weights(-10, category=True, style=True))
        self.assertEqual(self.reduce('post_purchase_negative', meta={'reason': 'too_expensive'}),
            empty_projection())

    def test_only_style_swipe_like_adds_unique_liked_lists_and_still_updates_weights(self):
        prior = self.taste(liked_categories=['old-category', 'рубашки'], liked_brands=['baon'],
            liked_colors=['white'], liked_styles=['minimalism'], disliked_categories=['old-dislike'])
        expected = weights(2, category=True, brand=True, color=True, style=True)
        expected.update(liked_categories=['old-category', 'рубашки'], liked_brands=['baon'],
            liked_colors=['white', 'black', 'navy'], liked_styles=['minimalism', 'casual', 'business'],
            disliked_categories=['old-dislike'])
        self.assertEqual(self.reduce('like', taste=prior, meta={'surface': 'style_swipe'}), expected)
        self.assertEqual(self.reduce('save', meta={'surface': 'style_swipe'}),
            weights(4, category=True, brand=True, color=True, style=True))
        self.assertEqual(self.reduce('dislike', meta={'surface': 'style_swipe', 'reason': 'dont_like_color'}),
            weights(-3, color=True))

    def test_explicit_weight_overrides_config_before_generic_dislike_attenuation(self):
        self.assertEqual(self.reduce('dislike', weight=-8), weights(-2, category=True, style=True))
        self.assertEqual(self.reduce('dislike', meta={'reason': 'dont_like_color'}, weight=-8),
            weights(-8, color=True))
        self.assertEqual(self.reduce('like', weight=0),
            weights(0, category=True, brand=True, color=True, style=True))

    def test_positive_weights_cap_at_fifty_without_affecting_unrelated_keys(self):
        prior = self.taste(category_weights={'рубашки': 49.5, 'футболки': 50, 'old': -7},
            brand_weights={'baon': 49.75}, color_weights={'black': 49.5, 'white': 50, 'navy': 49.9},
            style_weights={'casual': 50, 'minimalism': 49.5, 'business': 49.9})
        expected = weights(50, category=True, brand=True, color=True, style=True)
        expected['category_weights']['old'] = -7
        self.assertEqual(self.reduce('like', taste=prior), expected)

    def test_reason_targeted_negative_weights_cap_at_minus_fifty(self):
        prior = self.taste(category_weights={'рубашки': -49.5, 'футболки': -50},
            brand_weights={'baon': -49.5}, color_weights={'black': -49.5, 'white': -50, 'navy': -49.9},
            style_weights={'casual': -50, 'minimalism': -49.5, 'business': -49.9})
        for reason, fields in (
                ('dont_like_category', ('category_weights',)),
                ('dont_like_brand', ('brand_weights',)),
                ('dont_like_color', ('color_weights',)),
                ('not_my_style', ('category_weights', 'style_weights'))):
            with self.subTest(reason=reason):
                expected = deepcopy(vars(prior))
                for field in fields:
                    expected[field] = {key: -50 for key in expected[field]}
                self.assertEqual(self.reduce('dislike', taste=prior, meta={'reason': reason}), expected)

    def test_invalid_touched_weights_restart_at_zero_and_numeric_strings_are_usable(self):
        prior = self.taste(category_weights={'рубашки': 'broken', 'футболки': '3.5', 'untouched': 'invalid'},
            brand_weights={'baon': None}, color_weights={'black': '-48.5'}, style_weights={'casual': {}})
        expected = weights(2, category=True, brand=True, color=True, style=True)
        expected['category_weights'].update({'футболки': 5.5, 'untouched': 'invalid'})
        expected['color_weights']['black'] = -46.5
        self.assertEqual(self.reduce('like', taste=prior), expected)

    def test_pruning_retains_two_hundred_strongest_with_stable_equal_weight_ties(self):
        prior_weights = {'oldest-tie': 1, 'next-tie': -1}
        prior_weights.update({f'strong-{index:03}': -10 if index % 2 else 10 for index in range(198)})
        prior_weights.update({'last-tie': 1, 'invalid': 'not-numeric'})
        prior = self.taste(**{field: prior_weights.copy() for field in WEIGHT_FIELDS})
        no_features = LegacyProductFacts(categories=(), category='', brand='', colors=(), styles=())
        result = self.reduce('skip', taste=prior, facts=no_features)
        expected = {key: value for key, value in prior_weights.items() if key not in {'last-tie', 'invalid'}}
        for field in WEIGHT_FIELDS:
            with self.subTest(field=field):
                self.assertEqual(result[field], expected)
                self.assertEqual(len(result[field]), 200)
                self.assertIn('oldest-tie', result[field])
                self.assertIn('next-tie', result[field])
                self.assertIn('strong-001', result[field])
        self.assertEqual(prior.category_weights, prior_weights)

    def test_no_op_events_do_not_prune_existing_evidence(self):
        prior_weights = {f'evidence-{index}': index for index in range(201)}
        prior = self.taste(category_weights=prior_weights)
        self.assertEqual(self.reduce('impression', taste=prior), vars(prior))

    def test_projection_is_complete_and_detached_even_when_every_input_field_is_null(self):
        prior = SimpleNamespace(**{field: None for field in (*LIST_FIELDS, *WEIGHT_FIELDS)})
        result = self.reduce('impression', taste=prior)
        self.assertEqual(result, empty_projection())
        self.assertEqual(vars(prior), {field: None for field in (*LIST_FIELDS, *WEIGHT_FIELDS)})
        self.assertEqual(len({id(result[field]) for field in LIST_FIELDS}), 8)
        self.assertEqual(len({id(result[field]) for field in WEIGHT_FIELDS}), 4)

    def test_reducer_never_mutates_inputs_and_each_result_owns_its_containers(self):
        prior = self.taste(liked_categories=['existing'], category_weights={'рубашки': 1},
            disliked_styles=['old-style'], style_weights={'unrelated': -2})
        facts = sample_facts()
        meta = {'surface': 'style_swipe', 'request_context': {'filters': ['black']}}
        before, meta_before = deepcopy(vars(prior)), deepcopy(meta)
        first = self.reduce('like', taste=prior, facts=facts, meta=meta)
        second = self.reduce('like', taste=prior, facts=facts, meta=meta)
        self.assertEqual(first, second)
        self.assertEqual(vars(prior), before)
        self.assertEqual(meta, meta_before)
        for field in (*LIST_FIELDS, *WEIGHT_FIELDS):
            self.assertIsNot(first[field], getattr(prior, field))
            self.assertIsNot(first[field], second[field])
        first['liked_categories'].append('only-in-first')
        first['category_weights']['only-in-first'] = 99
        self.assertNotIn('only-in-first', second['liked_categories'])
        self.assertNotIn('only-in-first', second['category_weights'])
        self.assertEqual(vars(prior), before)
        with self.assertRaises(FrozenInstanceError):
            facts.brand = 'changed'

    def test_product_facts_normalize_explicit_evidence_without_editing_product(self):
        product = SimpleNamespace(category='Рубашки', category_name='Рубашки', title='Рубашка хлопковая',
            brand=' BAON ', colors=['чёрный', 'белый'], color_original='', style_tags=[' Casual ', 'Minimalism'])
        before = deepcopy(vars(product))
        facts = legacy_product_facts(product)
        self.assertEqual(facts, LegacyProductFacts(categories=('рубашки',), category='рубашки',
            brand='baon', colors=('black', 'white'), styles=('casual', 'minimalism')))
        self.assertEqual(vars(product), before)
        product.colors.append('red')
        product.style_tags.append('sport')
        self.assertEqual(facts.colors, ('black', 'white'))
        self.assertEqual(facts.styles, ('casual', 'minimalism'))


if __name__ == '__main__':
    unittest.main()
