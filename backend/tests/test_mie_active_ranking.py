"""Five-factor ranking on active surfaces, after admission and before composition."""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, event, insert, select
from sqlalchemy.orm import Session

from app.db import Base
from app.models import ActivationExposure, FitProfile, Product, RecommendationEventV2, TasteProfile, User
from app.schemas.shopping_intent import IntentSpec
from app.services import activation_service as activation
from app.services import intent_outfits, shopping_intent_service as intents
from app.services.personalization_scoring import AffinityScorer
from app.services.catalog_filters import CatalogFilters
from app.services.recommendation_config import FINAL_SCORE_WEIGHTS
from app.services.user_twin_service import DNA_TASTE_PREFIX


class ActiveMIERankingTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine, autoflush=False)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        self.user = User(id='active-mie-user', email='active-mie@example.test', password_hash='unused')
        self.db.add(self.user); self.db.flush()
        self.session = activation.configure(self.db, self.user, age_group='25-34', height=168,
            clothing_size='48', clothing_size_system='RU', shoe_size='38', shoe_size_system='EU',
            styles=['casual', 'minimalism'], price_segment='mid')
        self.session.completed_at = datetime.now(timezone.utc)
        self.fit = self.db.scalar(select(FitProfile).where(FitProfile.user_id == self.user.id))
        self.db.commit()

    def fields(self, pid, **changes):
        values = dict(id=pid, external_id=pid, source='baon', brand='BAON', title='Брюки', category='брюки',
            gender_target='womenswear', price=2000, currency='RUB', available_sizes=['48'],
            colors=['black'], style_tags=['casual'],
            feed_raw_json={'size_system': 'RU', 'color': 'black', 'fit': 'regular', 'silhouette': 'straight'},
            image_url=f'https://catalog.example.test/{pid}.jpg', product_url=f'https://catalog.example.test/p/{pid}',
            is_active=1, is_available=1, availability_status='in_stock', is_deleted_from_feed=0,
            last_seen_in_feed_at=datetime.now(timezone.utc))
        values.update(changes)
        return values

    def add(self, pid, **changes):
        product = Product(**self.fields(pid, **changes))
        self.db.add(product); self.db.flush()
        return product

    def test_first_feed_reports_real_five_factor_breakdown_and_does_not_record_delivery_as_exposure(self):
        self.fit.interest_categories = ['брюки']; self.db.flush()
        for index in range(25):
            self.add(f'first-{index:03d}')
        rows = activation.personalized_feed(self.db, self.user, self.session, limit=20)
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(set(row.breakdown), set(FINAL_SCORE_WEIGHTS))
            self.assertEqual(row.breakdown['fit_score'], .5, 'Confirmed size is eligibility, not perfect soft Fit')
            self.assertAlmostEqual(row.final_score, sum(FINAL_SCORE_WEIGHTS[key] * value for key, value in row.breakdown.items()))
        self.assertEqual(list(self.db.scalars(select(RecommendationEventV2))), [])

    def test_eighty_twenty_slate_uses_common_ranked_alternatives_and_keeps_exploitation_order(self):
        pool = [Product(**self.fields(f'rank-{index:03d}')) for index in range(60)]
        sources = {}
        result = activation.diverse_products(pool, 20, exploration=True,
            allow_category_fallback=True, selection_sources=sources)
        self.assertEqual(len(result), 20)
        self.assertEqual(len({product.id for product in result}), 20)
        self.assertEqual([index for index, product in enumerate(result)
            if sources[product.id] == 'soft_exploration'], [4, 9, 14, 19])
        remaining = list(pool)
        for product in result:
            index = remaining.index(product)
            if sources[product.id] == 'soft_exploration':
                self.assertGreaterEqual(index, len(remaining)//2)
                self.assertGreater(index, 0, 'Discovery selects an actual softer-ranked alternative')
            else:
                self.assertEqual(index, 0, 'Exploitation consumes the strongest remaining common-ranked choice')
            remaining.remove(product)

    def test_small_first_session_pages_preserve_twenty_percent_discovery_without_admitting_rejected_cards(self):
        for index in range(40):
            self.add(f'page-{index:03d}')
        bad_ids = {'bad-size', 'bad-stock', 'bad-gender'}
        self.add('bad-size', available_sizes=['46'], description='Подробная карточка')
        self.add('bad-stock', availability_status='unknown', description='Подробная карточка')
        self.add('bad-gender', gender_target='menswear', description='Подробная карточка')
        shown = []
        for limit in (7, 7, 6):
            page = activation.personalized_feed(self.db, self.user, self.session, limit=limit,
                exclude_ids=[row.product.id for row in shown], filters=CatalogFilters(categories=('bottoms',)))
            self.assertEqual(len(page), limit)
            for row in page:
                self.assertNotIn(row.product.id, bad_ids)
                self.assertEqual(set(row.breakdown), set(FINAL_SCORE_WEIGHTS))
                self.assertTrue(activation.product_ok(row.product, self.fit, self.session))
                activation.record_exposure_event(self.db, self.user, row.product.id, 'view', surface='feed')
            self.db.flush()
            shown.extend(page)
        self.assertEqual(len({row.product.id for row in shown}), 20)
        self.assertEqual([index for index, row in enumerate(shown)
            if 'soft_exploration' in row.candidate_sources], [4, 9, 14, 19])
        self.assertEqual(len(list(self.db.scalars(select(ActivationExposure)))), 20)

    def test_sparse_discovery_slot_falls_back_without_false_source_label_or_padding(self):
        product = Product(**self.fields('only-eligible'))
        sources = {}
        result = activation.diverse_products([product], 20, exploration=True,
            exploration_offset=4, allow_category_fallback=True, selection_sources=sources)
        self.assertEqual(result, [product])
        self.assertEqual(sources, {product.id: 'taste_match'})

    def test_replay_keeps_issued_ids_without_scoring_and_temporary_context_does_not_write_profiles(self):
        white = self.add('white', feed_raw_json={'size_system': 'RU', 'color': 'white'})
        black = self.add('black')
        taste = self.db.scalar(select(TasteProfile).where(TasteProfile.user_id == self.user.id))
        before = deepcopy((taste.category_weights, taste.color_weights, taste.style_weights,
            self.fit.recommended_silhouettes, self.fit.style_constraints))
        intent = intents.create_intent(self.db, self.user, text='Брюки до 7000, желательно чёрные', request_id='five-factor-intent-0001')
        issued = intents.intent_response(self.db, self.user, intent, remember=True)
        self.assertEqual(issued['products'][0]['product']['id'], black.id)
        self.assertIn(white.id, [row['product']['id'] for row in issued['products']])
        self.add('new-stronger-card', description='Полное описание товара')
        with patch.object(AffinityScorer, 'score', side_effect=AssertionError('GET must revalidate issued snapshot without ranking')):
            replay = intents.intent_response(self.db, self.user, intent)
        self.assertEqual([row['product']['id'] for row in replay['products']],
            [row['product']['id'] for row in issued['products']])
        self.assertEqual(before, (taste.category_weights, taste.color_weights, taste.style_weights,
            self.fit.recommended_silhouettes, self.fit.style_constraints))

    def test_cold_pool_keeps_early_retrieval_bound_and_skips_dna_without_semantic_inputs(self):
        self.db.execute(insert(Product), [self.fields(f'cold-{index:04d}') for index in range(1200)])
        self.db.flush()
        scored = []
        real_score = AffinityScorer.score
        def counted(scorer, product):
            scored.append(product.id)
            return real_score(scorer, product)
        with (patch.object(AffinityScorer, 'score', autospec=True, side_effect=counted),
              patch('app.services.personalization_scoring.reliable_taste_features', side_effect=AssertionError('Cold finalists do not need DNA'))):
            pool = activation.candidate_pool(self.db, self.user, self.session, taste_features={})
        self.assertEqual(len(pool), 50)
        self.assertEqual(len(scored), 50)
        self.assertEqual(scored, [f'cold-{index:04d}' for index in range(50)])

    def test_fifteen_thousand_candidates_have_bounded_cache_no_per_item_sql_and_late_winner(self):
        self.fit.recommended_silhouettes = ['straight']
        self.fit.avoid_silhouettes = ['wide']
        self.db.flush()
        early, late = 'a-early-winner', 'z-late-winner'
        rows = []
        for index in range(15001):
            pid = early if index == 0 else late if index == 15000 else f'mid-{index:05d}'
            winner = pid in {early, late}
            rows.append(self.fields(pid, description='Подробное описание' if winner else '',
                feed_raw_json={'size_system': 'RU', 'color': 'black',
                    'silhouette': 'straight' if winner else 'wide',
                    'material': 'cotton' if winner else 'linen'}))
        rejected = ['wrong-size', 'stale-stock', 'over-budget']
        rows.extend([self.fields(rejected[0], available_sizes=['46']),
            self.fields(rejected[1], last_seen_in_feed_at=datetime.now(timezone.utc)-timedelta(hours=73)),
            self.fields(rejected[2], price=7001)])
        self.db.execute(insert(Product), rows); self.db.commit(); self.db.expunge_all()
        user = self.db.get(User, 'active-mie-user')
        counts, scorers, statements = Counter(), [], []
        class CountingScorer(AffinityScorer):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs); scorers.append(self)
            def score(self, product):
                counts[product.id] += 1
                return super().score(product)
        def executed(_conn, _cursor, statement, _parameters, _context, _many):
            statements.append(statement)
        event.listen(self.engine, 'before_cursor_execute', executed)
        try:
            learned = {('material', DNA_TASTE_PREFIX + 'cotton'): (1, 1),
                ('material', DNA_TASTE_PREFIX + 'linen'): (-1, 1)}
            with (patch.object(intents, 'AffinityScorer', CountingScorer),
                  patch.object(intents, 'load_user_taste_feature_map', return_value=learned)):
                result = intents.item_results(self.db, user, IntentSpec(requested_categories=['брюки'], budget_max=7000))
        finally:
            event.remove(self.engine, 'before_cursor_execute', executed)
        self.assertEqual(len(counts), 15001, 'No arbitrary retrieval cutoff may hide late eligible matches')
        self.assertEqual(set(counts.values()), {1})
        self.assertFalse(set(rejected) & set(counts), 'Ranking cannot compensate for size, stock or budget failure')
        self.assertEqual(result[0]['product']['id'], late)
        self.assertIn(early, [row['product']['id'] for row in result])
        self.assertLessEqual(scorers[0].cached_products, 512)
        self.assertLessEqual(len(statements), 15, 'History/preferences are loaded once, not per candidate')

    def test_outfit_composition_reuses_early_and_cheap_finalist_scores_after_cache_eviction(self):
        self.fit.recommended_silhouettes = ['straight']; self.db.flush()
        early = 'a-early-top'
        tops = [self.fields(early if index == 0 else f'top-{index:04d}', category='рубашки', title='Рубашка',
            feed_raw_json={'size_system': 'RU', 'color': 'black', 'silhouette': 'straight' if index == 0 else 'wide'})
            for index in range(650)]
        self.db.execute(insert(Product), tops)
        self.add('bottom')
        self.add('shoes', category='обувь', title='Лоферы', available_sizes=['38'],
            feed_raw_json={'size_system': 'EU', 'color': 'black'})
        counts = Counter()
        real_score = AffinityScorer.score
        def counted(scorer, product):
            counts[product.id] += 1
            return real_score(scorer, product)
        with patch.object(AffinityScorer, 'score', autospec=True, side_effect=counted):
            result = intent_outfits.build_intent_outfits(self.db, self.user, IntentSpec(intent_type='outfit', budget_max=10000),
                intent_id='score-reuse-intent', version=1)
        self.assertTrue(result)
        self.assertEqual(result[0]['items']['top'], early)
        self.assertEqual(len(counts), 652)
        self.assertEqual(set(counts.values()), {1}, 'Composition must reuse retained common scores, including cheap finalists')


if __name__ == '__main__':
    unittest.main()
