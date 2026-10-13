"""Five-factor ranking uses evidence without replacing hard admission."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.db import Base
from app.models import Product, RecommendationEventV2, TasteProfile, User
from app.services.personalization_scoring import AffinityScorer, _quality, load_ranking_seen_counts
from app.services.recommendation_config import FINAL_SCORE_WEIGHTS
from app.services.recommendation_context import ShoppingContext
from app.services.user_twin_service import DNA_TASTE_PREFIX


class FiveFactorRankingTests(unittest.TestCase):
    def setUp(self):
        self.profile = SimpleNamespace(style_preferences=[])
        self.taste = TasteProfile(category_weights={}, brand_weights={},
            color_weights={}, style_weights={})

    def product(self, **changes):
        values = dict(id='ranking-product', title='Рубашка', category='рубашки',
            category_name='рубашки', source='test-shop', brand='', price=2000,
            colors=['black'], style_tags=['casual'], feed_raw_json={}, raw_params_json={},
            image_url='https://catalog.example.test/item.jpg',
            product_url='https://catalog.example.test/item', available_sizes=['M'])
        values.update(changes)
        return Product(**values)

    def fit(self, **changes):
        values = dict(recommended_silhouettes=[], avoid_silhouettes=[], avoid_fit=[],
            recommended_fit='regular', height_cm=170, clothing_size='M',
            body_measurements_json={}, color_palette=[], avoid_colors=[])
        values.update(changes)
        return SimpleNamespace(**values)

    def evaluate(self, product=None, **kwargs):
        return AffinityScorer(self.profile, self.taste, **kwargs).evaluate(
            product if product is not None else self.product())

    def test_final_score_is_the_weighted_sum_of_all_five_components(self):
        value = self.product(brand='BAON', description='Материал и уход указаны.',
            feed_raw_json={'silhouette': 'straight', 'style': 'casual'})
        evaluation = self.evaluate(value, fit=self.fit(recommended_silhouettes=['straight']),
            context=ShoppingContext(), seen_counts={value.id: 2})
        self.assertEqual(set(evaluation.breakdown), set(FINAL_SCORE_WEIGHTS))
        for component, weight in FINAL_SCORE_WEIGHTS.items():
            score = evaluation.breakdown[component]
            self.assertEqual(score, getattr(evaluation, component))
            self.assertGreaterEqual(score, 0)
            self.assertLessEqual(score, 1)
            self.assertGreater(weight, 0)
        self.assertAlmostEqual(sum(FINAL_SCORE_WEIGHTS.values()), 1)
        self.assertAlmostEqual(evaluation.affinity,
            sum(FINAL_SCORE_WEIGHTS[key] * score for key, score in evaluation.breakdown.items()))
        for name in ('taste_reasons', 'fit_reasons', 'context_reasons',
                'quality_reasons', 'exploration_reasons'):
            self.assertIsInstance(getattr(evaluation, name), tuple)
        changed = evaluation.breakdown
        changed['fit_score'] = -100
        self.assertGreaterEqual(evaluation.breakdown['fit_score'], 0)

    def test_fit_matches_only_reliable_supported_garment_evidence(self):
        supported = self.product(feed_raw_json={'silhouette': 'straight'})
        legacy_only = self.product(silhouette='straight')
        fit = self.fit(recommended_silhouettes=['straight'])
        self.assertAlmostEqual(self.evaluate(supported, fit=fit).fit_score, .7)
        self.assertAlmostEqual(self.evaluate(legacy_only, fit=fit).fit_score, .5)
        self.assertTrue(self.evaluate(supported, fit=fit).fit_reasons)
        self.assertFalse(self.evaluate(legacy_only, fit=fit).fit_reasons)

    def test_avoided_silhouette_and_fit_are_soft_penalties(self):
        value = self.product(feed_raw_json={'silhouette': 'straight', 'fit': 'slim'})
        silhouette = self.evaluate(value, fit=self.fit(avoid_silhouettes=['straight']))
        construction = self.evaluate(value, fit=self.fit(avoid_fit=['slim']))
        self.assertAlmostEqual(silhouette.fit_score, .25)
        self.assertAlmostEqual(construction.fit_score, .3)
        self.assertGreater(silhouette.affinity, 0)
        self.assertGreater(construction.affinity, 0)
        self.assertTrue(silhouette.fit_reasons)
        self.assertTrue(construction.fit_reasons)

    def test_conflicting_unknown_and_noncanonical_fit_evidence_stays_neutral(self):
        fit = self.fit(recommended_silhouettes=['straight'], avoid_fit=['slim'])
        products = [self.product(), self.product(feed_raw_json={
            'silhouette': ['straight', 'a_line'], 'fit': ['slim', 'relaxed']}),
            self.product(raw_params_json={'unrecognized_attribute': 'straight slim'})]
        for value in products:
            with self.subTest(raw=value.feed_raw_json):
                evaluation = self.evaluate(value, fit=fit)
                self.assertAlmostEqual(evaluation.fit_score, .5)
                self.assertFalse(evaluation.fit_reasons)
        value = self.product(feed_raw_json={'silhouette': 'straight', 'fit': 'slim'})
        unknown_profile = self.fit(recommended_silhouettes=['unknown', 'body_shape'],
            avoid_silhouettes=['not straight'], avoid_fit=['unrecognized'])
        self.assertAlmostEqual(self.evaluate(value, fit=unknown_profile).fit_score, .5)

    def test_default_fit_body_size_gender_and_color_are_not_fit_points(self):
        value = self.product(feed_raw_json={'fit': 'regular'},
            gender_target='womenswear', available_sizes=['M'], price=100)
        supplied = self.fit(height_cm=182, clothing_size='M', gender_target='womenswear',
            budget_max=10000, body_measurements_json={'waist': 70, 'hips': 95},
            color_palette=['black'], avoid_colors=['black'])
        self.assertAlmostEqual(self.evaluate(value).fit_score, .5)
        self.assertAlmostEqual(self.evaluate(value, fit=supplied).fit_score, .5)
        self.assertFalse(self.evaluate(value, fit=supplied).fit_reasons)

    def test_quality_measures_card_completeness_without_inventing_image_precision(self):
        basic = self.product()
        with_brand = self.product(brand='BAON')
        complete = self.product(brand='BAON', description='Состав и уход за изделием.')
        self.assertAlmostEqual(self.evaluate(basic).quality_score, .7)
        self.assertAlmostEqual(self.evaluate(with_brand).quality_score, .85)
        self.assertAlmostEqual(self.evaluate(complete).quality_score, 1)
        for measurements in (None, 0, 1, 999):
            with self.subTest(image_quality_score=measurements):
                value = self.product(image_quality_score=measurements)
                self.assertAlmostEqual(self.evaluate(value).quality_score, .7)
        empty = self.product(title='', category='', category_name='', image_url='',
            product_url='', brand='  ', description='  ')
        self.assertAlmostEqual(self.evaluate(empty).quality_score, 0)

    def test_quality_does_not_turn_stock_size_or_price_into_card_quality(self):
        good = self.product(price=1000, available_sizes=['M'], availability_status='in_stock',
            last_seen_in_feed_at=datetime.now(timezone.utc), is_active=1, is_available=1)
        unavailable = self.product(price=1000000, available_sizes=[], availability_status='unknown',
            last_seen_in_feed_at=None, is_active=0, is_available=0)
        self.assertEqual(self.evaluate(good).quality_score, self.evaluate(unavailable).quality_score)

    def test_original_url_only_card_keeps_quality_credit_for_its_accepted_outbound_link(self):
        from app.services.catalog.catalog_quality import product_has_purchasable_link

        value = self.product(product_url='', affiliate_url=None,
            original_url='https://merchant.example.test/product/item')
        self.assertTrue(product_has_purchasable_link(value))
        self.assertAlmostEqual(self.evaluate(value).quality_score, .7)
        value.original_url = ''
        self.assertFalse(product_has_purchasable_link(value))
        self.assertAlmostEqual(self.evaluate(value).quality_score, .5)

    def test_quality_reads_only_card_fields(self):
        class CardOnly:
            title = 'Рубашка'
            category = 'рубашки'
            category_name = None
            image_url = 'https://catalog.example.test/item.jpg'
            product_url = 'https://catalog.example.test/item'
            affiliate_url = None
            original_url = None
            brand = ''
            description = None

            def __getattr__(self, field):
                raise AssertionError('Quality must not read ' + field)

        quality, reasons = _quality(CardOnly())
        self.assertAlmostEqual(quality, .7)
        self.assertEqual(reasons, ())

    def test_exploration_is_deterministic_bounded_and_reduced_by_visible_history(self):
        value = self.product()
        fresh = self.evaluate(value)
        once = self.evaluate(value, seen_counts={value.id: 1})
        often = self.evaluate(value, seen_counts={value.id: 10000})
        self.assertAlmostEqual(fresh.exploration_score, .9)
        self.assertAlmostEqual(once.exploration_score, .82)
        self.assertAlmostEqual(often.exploration_score, .6)
        self.assertEqual(fresh, self.evaluate(value))
        self.assertGreater(fresh.exploration_score, once.exploration_score)
        self.assertGreater(once.exploration_score, often.exploration_score)
        self.assertAlmostEqual(fresh.affinity - often.affinity,
            FINAL_SCORE_WEIGHTS['exploration_score'] * .3)
        self.assertLessEqual(FINAL_SCORE_WEIGHTS['exploration_score'] * fresh.exploration_score, .05)

    def test_confident_known_positive_reduces_exploration_and_legacy_item_implies_seen(self):
        value = self.product()
        confidence_only = {('category', value.category): (0, .9)}
        positive = {('category', value.category): (1, .9)}
        neutral = self.evaluate(value, taste_features=confidence_only)
        known = self.evaluate(value, taste_features=positive)
        self.assertAlmostEqual(neutral.exploration_score - known.exploration_score, .1)
        legacy_item = {('item', value.id): (0, 0)}
        implicit = self.evaluate(value, taste_features=legacy_item)
        explicit = self.evaluate(value, taste_features=legacy_item, seen_counts={value.id: 1})
        self.assertEqual(implicit.exploration_score, explicit.exploration_score)
        self.assertLess(implicit.exploration_score, self.evaluate(value).exploration_score)

    def test_task_preferences_change_context_without_mutating_taste_or_fit(self):
        value = self.product(title='Платье', category='платья', category_name='платья',
            colors=['blue'], style_tags=['elegant'],
            feed_raw_json={'color': 'blue', 'style': 'elegant'})
        fit = self.fit()
        before_taste = deepcopy({field: getattr(self.taste, field) for field in
            ('category_weights', 'style_weights', 'color_weights', 'brand_weights')})
        before_fit = deepcopy(vars(fit))
        ordinary = self.evaluate(value, fit=fit, context=ShoppingContext())
        requested = self.evaluate(value, fit=fit, context=ShoppingContext(),
            preferred_colors=['blue'], preferred_styles=['elegant'], requested_categories=['платья'])
        self.assertGreater(requested.context_score, ordinary.context_score)
        self.assertEqual(requested.taste_score, ordinary.taste_score)
        self.assertEqual(requested.fit_score, ordinary.fit_score)
        self.assertEqual(requested.quality_score, ordinary.quality_score)
        self.assertEqual(requested.exploration_score, ordinary.exploration_score)
        self.assertAlmostEqual(requested.affinity - ordinary.affinity,
            FINAL_SCORE_WEIGHTS['context_score'] * (requested.context_score - ordinary.context_score))
        self.assertEqual(before_fit, vars(fit))
        self.assertEqual(before_taste, {key: getattr(self.taste, key) for key in before_taste})

    def test_mutable_request_inputs_are_copied_before_evaluation(self):
        value = self.product(colors=['blue'], feed_raw_json={'color': 'blue'})
        seen = {value.id: 1}
        learned = {('material', DNA_TASTE_PREFIX + 'cotton'): (1, 1)}
        colors, styles, categories = ['blue'], [], ['рубашки']
        scorer = AffinityScorer(self.profile, self.taste, seen_counts=seen,
            taste_features=learned, context=ShoppingContext(), preferred_colors=colors,
            preferred_styles=styles, requested_categories=categories)
        seen[value.id] = 1000
        learned.clear()
        colors.clear()
        styles.append('sporty')
        categories.clear()
        reference = self.evaluate(value, seen_counts={value.id: 1},
            taste_features={('material', DNA_TASTE_PREFIX + 'cotton'): (1, 1)},
            context=ShoppingContext(), preferred_colors=['blue'], requested_categories=['рубашки'])
        self.assertEqual(scorer.evaluate(value), reference)

    def test_cache_remains_bounded_and_does_not_cross_requests(self):
        first = AffinityScorer(self.profile, self.taste, seen_counts={'item-5': 100}, cache_size=2)
        second = AffinityScorer(self.profile, self.taste, cache_size=2)
        values = [self.product(id=f'item-{index}') for index in range(6)]
        for value in values:
            first.evaluate(value)
            second.evaluate(value)
        self.assertEqual(first.cached_products, 2)
        self.assertEqual(second.cached_products, 2)
        cached = first.evaluate(values[-1])
        self.assertIs(first.evaluate(values[-1]), cached)
        self.assertLess(cached.exploration_score, second.evaluate(values[-1]).exploration_score)


class RankingVisibleHistoryTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine, autoflush=False)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        self.now = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
        self.user = User(id='ranking-history-user', email='ranking-history@example.test', password_hash='unused')
        self.other = User(id='ranking-history-other', email='ranking-other@example.test', password_hash='unused')
        self.db.add_all([self.user, self.other])
        self.db.flush()
        self.event_index = 0

    def action(self, product_id, kind, *, user=None, when=None):
        self.event_index += 1
        self.db.add(RecommendationEventV2(id=f'event-{self.event_index}',
            user_id=(user or self.user).id, product_id=product_id, event_type=kind,
            created_at=when if when is not None else self.now - timedelta(hours=1), meta_json={}))

    def test_history_is_owner_scoped_unique_product_day_and_excludes_invisible_and_future_events(self):
        for kind in ('view', 'view', 'like', 'open_product', 'save'):
            self.action('seen', kind)
        self.action('seen', 'affiliate_click', when=self.now - timedelta(days=1))
        self.action('seen', 'skip', when=self.now - timedelta(days=2))
        self.action('seen', 'view', when=self.now - timedelta(days=91))
        self.action('seen', 'view', when=self.now + timedelta(hours=1))
        self.action('impression-only', 'impression')
        self.action('future-only', 'like', when=self.now + timedelta(hours=1))
        self.action('stale-only', 'view', when=self.now - timedelta(days=91))
        self.action('other-only', 'view', user=self.other)
        self.action(None, 'view')
        self.db.flush()
        self.assertEqual(load_ranking_seen_counts(self.db, user_id=self.user.id, as_of=self.now), {'seen': 3})
        self.assertEqual(load_ranking_seen_counts(self.db, user_id=self.other.id, as_of=self.now), {'other-only': 1})

    def test_history_uses_one_aggregate_read_and_no_persistent_writes(self):
        for index in range(25):
            self.action(f'item-{index}', 'view')
        self.db.flush()
        statements = []
        def executed(_connection, _cursor, statement, _parameters, _context, _many):
            statements.append(statement)
        event.listen(self.engine, 'before_cursor_execute', executed)
        try:
            counts = load_ranking_seen_counts(self.db, user_id=self.user.id, as_of=self.now)
        finally:
            event.remove(self.engine, 'before_cursor_execute', executed)
        self.assertEqual(len(counts), 25)
        self.assertEqual(len(statements), 1)
        self.assertTrue(statements[0].lstrip().upper().startswith('SELECT'))
        self.assertIn('GROUP BY', statements[0].upper())
        self.assertFalse(self.db.new or self.db.dirty or self.db.deleted)


if __name__ == '__main__':
    unittest.main()
