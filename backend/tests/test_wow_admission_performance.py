"""Wow proves mandatory footwear admission before full-catalog ranking."""
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, event, insert, select
from sqlalchemy.orm import Session

from app.db import Base
from app.models import ActivationSession, FitProfile, Product, SourceRule, User
from app.services import activation_service as activation
from app.services import outfit_engine_v2 as outfits
from app.services.personalization_scoring import AffinityScorer
from app.services.recommendation_context import ShoppingContext


class WowAdmissionPerformanceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine, autoflush=False)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        self.user = User(id='wow-admission-user', email='wow-admission@example.test', password_hash='unused')
        self.db.add(self.user); self.db.flush()
        self.session = activation.configure(self.db, self.user, gender_target='womenswear', age_group='25-34', height=170,
            clothing_size='M', clothing_size_system='LETTER', shoe_size='38', shoe_size_system='EU',
            styles=['casual', 'minimalism'], price_segment='mass')
        self.fit = self.db.scalar(select(FitProfile).where(FitProfile.user_id == self.user.id))

    def fields(self, pid, *, shoe=False, **changes):
        values = dict(id=pid, external_id=pid, source='baon', brand='BAON',
            title='Лоферы кожаные' if shoe else 'Рубашка', category='обувь' if shoe else 'рубашки',
            price=1500, currency='RUB', gender_target='womenswear',
            available_sizes=['38'] if shoe else ['M'],
            feed_raw_json={'size_system': 'EU' if shoe else 'LETTER', 'material': 'cotton'},
            colors=['black'], style_tags=['casual', 'minimalism', 'elegant'],
            image_url=f'https://catalog.example.test/{pid}.jpg',
            product_url=f'https://catalog.example.test/p/{pid}',
            is_active=1, is_available=1, availability_status='in_stock', is_deleted_from_feed=0,
            last_seen_in_feed_at=datetime.now(timezone.utc))
        values.update(changes)
        return values

    def add(self, pid, **changes):
        value = Product(**self.fields(pid, **changes))
        self.db.add(value); self.db.flush()
        return value

    def pool(self, **kwargs):
        return activation.candidate_pool(self.db, self.user, self.session,
            required_roles={'shoes'}, context=ShoppingContext(), **kwargs)

    def test_strict_footwear_probe_avoids_clothing_loads_and_dna_scoring(self):
        clothing = [self.fields(f'clothing-{index:05d}',
            category='брюки' if index % 2 else 'рубашки',
            title='Брюки прямые' if index % 2 else 'Рубашка') for index in range(1200)]
        self.db.execute(insert(Product), clothing)
        shoes = [self.fields('wrong-size', shoe=True, available_sizes=['37']),
            self.fields('unknown-system', shoe=True, feed_raw_json={}, available_sizes=['38']),
            self.fields('unknown-size-stock', shoe=True, available_sizes_detailed=[
                {'raw_size': '38', 'availability': 'unknown'}])]
        self.db.execute(insert(Product), shoes); self.db.flush()
        self.session.swipe_product_ids = [row['id'] for row in clothing[:15]]
        for position, pid in enumerate(self.session.swipe_product_ids):
            activation.swipe_feedback(self.db, self.user, self.session, pid,
                'like' if position < 10 else 'dislike')
        self.db.commit(); self.db.expunge_all()
        user = self.db.get(User, 'wow-admission-user')
        session = self.db.get(ActivationSession, user.id)
        loaded = []
        def on_load(product, _context):
            loaded.append(product.id)
        event.listen(Product, 'load', on_load)
        try:
            with (patch.object(AffinityScorer, 'score', side_effect=AssertionError('Incomplete Wow cannot rank')),
                    patch.object(AffinityScorer, 'evaluate', side_effect=AssertionError('Incomplete Wow cannot parse DNA'))):
                result = activation.candidate_pool(self.db, user, session,
                    required_roles={'shoes'}, context=ShoppingContext())
            self.assertEqual(result, [])
            self.assertTrue(set(loaded) <= {'unknown-system', 'unknown-size-stock'})
            self.assertTrue(loaded, 'The exact footwear admission gate must examine the available shoe rows.')
        finally:
            event.remove(Product, 'load', on_load)

    def test_all_existing_hard_gates_reject_footwear_before_scoring(self):
        self.db.add(SourceRule(id='wow-blocked-source-rule', source_id='wow-blocked-source',
            rule_type='blocked_keyword', rule_value='лофер', is_active=1))
        self.db.flush()
        cases = {
            'wrong_system': {'feed_raw_json': {'size_system': 'RU'}},
            'conflicting_system': {'feed_raw_json': {'size_system': ['EU', 'RU']}},
            'per_size_unavailable': {'available_sizes_detailed': [{'raw_size': '38', 'availability': 'unavailable'}]},
            'unknown_stock': {'last_seen_in_feed_at': None},
            'stale_stock': {'last_seen_in_feed_at': datetime.now(timezone.utc) - timedelta(hours=73)},
            'unknown_availability': {'availability_status': 'unknown'},
            'wrong_gender': {'gender_target': 'menswear'},
            'over_budget': {'price': 10001},
            'private_image': {'image_url': 'http://127.0.0.1/shoe.jpg'},
            'missing_link': {'product_url': ''},
            'raw_children': {'raw_params_json': {'Возраст': 'для девочек'}},
            'sports_lead': {'description': 'Спортивная обувь для фитнеса.'},
            'currency': {'currency': 'USD'},
            'excluded_source': {'source': 'demix'},
            'source_rule': {'source_id': 'wow-blocked-source'},
        }
        for label, changes in cases.items():
            with self.subTest(case=label):
                shoe = self.add(label, shoe=True, **changes)
                with patch.object(AffinityScorer, 'score', side_effect=AssertionError('Hard-rejected shoes cannot rank')):
                    self.assertEqual(self.pool(), [])
                self.db.delete(shoe); self.db.flush()

    def test_hidden_clothing_identity_does_not_admit_a_shoe_alias_or_load_clothing(self):
        hidden = self.add('hidden-clothing')
        alias = self.add('shoe-alias', shoe=True, image_url=hidden.image_url)
        hidden_id, alias_id = hidden.id, alias.id
        self.session.swipes_json = {hidden_id: 'dislike'}
        self.db.commit(); self.db.expunge_all()
        self.user = self.db.get(User, 'wow-admission-user')
        self.session = self.db.get(ActivationSession, self.user.id)
        loaded = []
        def on_load(product, _context):
            loaded.append(product.id)
        event.listen(Product, 'load', on_load)
        try:
            with patch.object(AffinityScorer, 'score', side_effect=AssertionError('A hidden identity cannot rank')):
                self.assertEqual(self.pool(), [])
            self.assertEqual(loaded, [alias_id])
        finally:
            event.remove(Product, 'load', on_load)

    def test_brand_shoes_text_cannot_turn_a_top_into_mandatory_footwear(self):
        self.add('wrong-role', title='Майка DC Shoes', category='рубашки', subcategory='обувь')
        with patch.object(AffinityScorer, 'score', side_effect=AssertionError('A top cannot fill shoes')):
            self.assertEqual(self.pool(), [])

    def test_role_is_rechecked_when_the_full_product_changes_after_thin_projection(self):
        shoe = self.add('changed-role', shoe=True)
        self.db.commit(); self.db.expunge_all()
        self.user = self.db.get(User, 'wow-admission-user')
        self.session = self.db.get(ActivationSession, self.user.id)
        def on_load(product, _context):
            if product.id == 'changed-role':
                product.title = 'Рубашка'
        event.listen(Product, 'load', on_load)
        try:
            with patch.object(AffinityScorer, 'score', side_effect=AssertionError('A stale role cannot rank')):
                self.assertEqual(self.pool(), [])
        finally:
            event.remove(Product, 'load', on_load)

    def test_english_merchant_path_and_subcategory_roles_preserve_normal_positive_pool(self):
        cases = [dict(title='Loafers', category='рубашки'),
            dict(title='Лоферы', category='Женщинам / Обувь / Лоферы'),
            dict(title='BAON model', category='рубашки', subcategory='обувь')]
        for index, changes in enumerate(cases):
            with self.subTest(case=index):
                shoe = self.add(f'positive-role-{index}', shoe=True, **changes)
                self.assertEqual([product.id for product in self.pool()], [shoe.id])
                self.db.delete(shoe); self.db.flush()

    def test_late_exact_shoe_preserves_positive_outfit_items_scores_and_reasons(self):
        self.add('top', title='Рубашка однотонная', category='рубашки')
        self.add('bottom', title='Брюки прямые', category='брюки')
        self.add('dress', title='Платье миди', category='платья')
        rejected = [self.fields(f'a-shoe-{index:05d}', shoe=True, available_sizes=['37']) for index in range(1200)]
        self.db.execute(insert(Product), rejected)
        late = self.add('z-late-shoe', shoe=True)
        def selected(scenario):
            buckets = outfits._build_slot_candidates(self.db, self.user, scenario=scenario,
                fit=self.fit, excluded=set(), skipped_ids=set(), per_slot=10)
            candidates = outfits._build_combinations(buckets, scenario=scenario, fit=self.fit, limit=2)
            candidates = [candidate for candidate in candidates if candidate.total_price <= self.fit.budget_max]
            return [(tuple((slot, item.product.id) for slot, item in candidate.items.items()),
                candidate.total_price, candidate.final_score, candidate.reasons)
                for candidate in outfits._diverse_top(candidates, limit=2)]
        for scenario in ('daily', 'evening'):
            with self.subTest(scenario=scenario):
                actual = selected(scenario)
                with patch.object(activation, '_required_roles_available', return_value=True):
                    previous = selected(scenario)
                self.assertTrue(actual)
                self.assertEqual(actual, previous)
                self.assertTrue(all(('shoes', late.id) in items for items, *_ in actual))

    def test_positive_early_return_closes_the_streamed_role_result(self):
        self.db.execute(insert(Product), [self.fields(f'shoe-{index:05d}', shoe=True)
            for index in range(600)])
        self.db.flush()
        role_results = []
        execute = self.db.execute
        def tracked_execute(statement, *args, **kwargs):
            result = execute(statement, *args, **kwargs)
            if statement.get_execution_options().get('yield_per') == 500:
                role_results.append(result)
            return result
        with patch.object(self.db, 'execute', side_effect=tracked_execute):
            self.assertTrue(activation._required_roles_available(self.db,
                required_roles={'shoes'}, conditions=[], hidden=set(), hidden_keys=set(),
                fit=self.fit, session=self.session, rules={}, filters=None, context=ShoppingContext()))
        self.assertEqual(len(role_results), 1)
        self.assertTrue(role_results[0].closed, 'Early admission must close the server-side role cursor.')

    def test_unflushed_product_role_change_keeps_the_existing_orm_read_behavior(self):
        product = self.add('pending-role-change')
        product.title = 'Loafers'
        product.available_sizes = ['38']
        product.feed_raw_json = {'size_system': 'EU'}
        self.assertIn(product, self.db.dirty)
        normal = activation.candidate_pool(self.db, self.user, self.session, context=ShoppingContext())
        with patch.object(activation, '_required_roles_available',
                side_effect=AssertionError('A stale thin projection cannot prove absence')):
            required = self.pool()
        self.assertEqual([row.id for row in normal], [product.id])
        self.assertEqual([row.id for row in required], [row.id for row in normal])

    def test_multiple_mandatory_roles_probe_shoes_before_loading_top_or_bottom(self):
        self.add('top')
        self.add('bottom', title='Брюки', category='брюки')
        self.add('wrong-shoe', shoe=True, available_sizes=['37'])
        self.db.commit(); self.db.expunge_all()
        self.user = self.db.get(User, 'wow-admission-user')
        self.session = self.db.get(ActivationSession, self.user.id)
        loaded = []
        def on_load(product, _context):
            loaded.append(product.id)
        event.listen(Product, 'load', on_load)
        try:
            for scenario in ('gym', 'run'):
                roles = set.intersection(*(set(option) for option in outfits.SCENARIO_SLOT_OPTIONS[scenario]))
                with patch.object(AffinityScorer, 'score', side_effect=AssertionError('Missing mandatory footwear cannot rank')):
                    self.assertEqual(activation.candidate_pool(self.db, self.user, self.session,
                        required_roles=roles, context=ShoppingContext()), [])
            self.assertEqual(loaded, [], 'Impossible flat sizes must not hydrate merchant blobs.')
        finally:
            event.remove(Product, 'load', on_load)

    def test_untracked_live_size_mutation_remains_visible_to_role_probe(self):
        shoe = self.add('live-size', shoe=True, available_sizes=['37'])
        self.db.commit()
        shoe.available_sizes.append('38')
        self.assertFalse(self.db.dirty)
        self.assertEqual([product.id for product in self.pool()], [shoe.id])


if __name__ == '__main__':
    unittest.main()
