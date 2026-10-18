"""Affordable complete purchases survive bounded high-affinity shortlists.

Every positive SKU/size/price is labelled while building the synthetic catalogue;
the expected set never calls the implementation's eligibility or size helpers.
"""
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, delete, event, insert, select
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.models import ActivationSession, FitProfile, Product, SourceRule, TasteProfile, User
from app.services import activation_service as activation, outfit_engine_v2 as outfits
from app.services import outfit_session
from app.services.personalization_scoring import AffinityScorer
from app.services.recommendation_context import context_for
from app.services.product_identity import product_identity_keys
import test_intent_outfit_create_session as fixtures
import test_outfit_session as outfit_fixtures


class OutfitAffordableCandidatesTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, autoflush=False)
        self.db = self.sessions()
        self.addCleanup(self.db.close)
        self.user_id = outfit_fixtures.seed_outfit_refresh_catalogue(self.db)
        self.db.execute(delete(Product).where(Product.id.in_(fixtures.BAD_IDS)))
        self.user = self.db.get(User, self.user_id)
        self.fit = self.db.scalar(select(FitProfile).where(FitProfile.user_id == self.user_id))
        taste = self.db.scalar(select(TasteProfile).where(TasteProfile.user_id == self.user_id))
        taste.color_weights = {'red': 6}
        taste.brand_weights = {'favourite brand': 6}
        self.good = {f'good-{role}-{index}' for role in ('top', 'bottom', 'shoes')
            for index in range(3)}
        self.db.commit()

    @staticmethod
    def fields(pid, role, **changes):
        category, title = {
            'top': ('рубашки', 'Женская рубашка хлопковая'),
            'bottom': ('брюки', 'Женские брюки прямые'),
            'shoes': ('обувь', 'Женские лоферы кожаные'),
        }[role]
        fields = dict(id=pid, external_id=pid, source='baon', source_id='outfit-merchant',
            title=title, category=category, brand='Favourite brand', price=Decimal('4000'),
            currency='RUB', gender_target='womenswear', colors=['red'],
            style_tags=['casual', 'minimalism'], available_sizes=['38' if role == 'shoes' else 'M'],
            feed_raw_json={'size_system': 'EU' if role == 'shoes' else 'LETTER'},
            image_url=f'https://baon.ru/images/{pid}.jpg',
            product_url=f'https://baon.ru/products/{pid}', is_active=1, is_available=1,
            is_deleted_from_feed=0, availability_status='in_stock',
            last_seen_in_feed_at=datetime.now(timezone.utc))
        fields.update(changes)
        return fields

    def distractors(self, count=55):
        self.db.execute(insert(Product), [self.fields(f'expensive-{role}-{index:04}', role)
            for role in ('top', 'bottom', 'shoes') for index in range(count)])
        self.db.commit()

    def buckets(self, *, scenario='daily'):
        return outfits._build_slot_candidates(self.db, self.user, scenario=scenario,
            fit=self.fit, excluded=set(), skipped_ids=set(), per_slot=10)

    def assert_affordable_batch(self, result):
        self.assertTrue(result, 'The independent catalogue contains complete 5001-RUB purchases')
        self.assertLessEqual(len(result), 3)
        for row in result:
            self.assertEqual(set(row['items']), {'top', 'bottom', 'shoes'})
            self.assertLessEqual(set(row['items'].values()), self.good)
            actual = sum((Decimal(str(p['price'])) for p in row['products'].values()), Decimal(0))
            self.assertEqual(Decimal(str(row['total_price'])), actual)
            self.assertLessEqual(actual, Decimal('6000'))
            for slot, product in row['products'].items():
                self.assertEqual(product['available_sizes'], ['38' if slot == 'shoes' else 'M'])

    def test_outfit_only_category_reserve_keeps_late_cheap_skus_beyond_fifty_high_scores(self):
        self.distractors()
        session = self.db.get(ActivationSession, self.user_id)
        normal = activation.candidate_pool(self.db, self.user, session, context=context_for('daily'))
        self.assertEqual(len(normal), 150)
        self.assertTrue(self.good.isdisjoint({p.id for p in normal}))
        evaluations = {}
        reserved = activation.candidate_pool(self.db, self.user, session, context=context_for('daily'),
            outfit_scenario='daily', outfit_evaluations=evaluations)
        self.assertEqual(len(reserved), 150)
        self.assertLessEqual(self.good, {p.id for p in reserved})
        self.assertEqual(set(evaluations), set(reserved))
        for role in ('top', 'bottom', 'shoes'):
            self.assertEqual(sum(p.id.startswith(f'expensive-{role}-') for p in reserved), 47)

    def test_normal_refresh_finds_full_purchase_and_rank_remains_read_only_bounded(self):
        self.distractors()
        statements, bucket_sizes = [], []
        original = outfits._build_combinations

        def compose(buckets, **options):
            self.assertTrue(options['db'].info['recommendation_read_only'])
            self.assertNotIn('_recommendation_owner_mutations', options['db'].info)
            bucket_sizes.append({slot: len(rows) for slot, rows in buckets.items()})
            self.assertTrue(all(len(rows) <= 10 for rows in buckets.values()))
            return original(buckets, **options)

        def record(_connection, _cursor, sql, *_unused):
            statements.append(sql.strip().split()[0].upper())

        event.listen(self.engine, 'before_cursor_execute', record)
        try:
            with patch.object(outfits, '_build_combinations', side_effect=compose):
                proposal = outfit_session._prepare(self.sessions, self.user_id, count=3, scenario='daily')
        finally:
            event.remove(self.engine, 'before_cursor_execute', record)
        self.assertTrue(proposal.choices)
        self.assertEqual(set(statements), {'SELECT'})
        self.assertTrue(bucket_sizes)
        for choice in proposal.choices:
            self.assertLessEqual({item.product_id for item in choice.items}, self.good)
            self.assertLessEqual(choice.total_price, Decimal('6000'))
        self.db.close()
        self.assert_affordable_batch(outfit_session.build_outfits_response(self.engine, self.user_id, count=3))

    def test_cheap_reserve_does_not_replace_the_seven_strongest_or_shorten_on_overlap(self):
        self.distractors()
        for slot, rows in self.buckets().items():
            if slot == 'one_piece':
                self.assertEqual(rows, [])
                continue
            self.assertEqual(len(rows), 10)
            self.assertEqual(sum(row.product.id.startswith('expensive-') for row in rows), 7)
            self.assertEqual({row.product.id for row in rows if row.product.id.startswith('good-')},
                {f'good-{slot}-{i}' for i in range(3)})
        # If the cheapest are also strongest, their duplicate places are filled
        # from the remaining strongest candidates; no artificial short page.
        for product in self.db.scalars(select(Product).where(Product.id.like('expensive-%'))):
            product.price = Decimal('799.00')
        self.db.commit()
        for slot, rows in self.buckets().items():
            if slot != 'one_piece':
                self.assertEqual(len(rows), 10)
                self.assertTrue(all(row.product.id.startswith('expensive-') for row in rows))

    def test_invalid_cheapest_skus_cannot_reserve_places_or_enter_an_office_outfit(self):
        self.distractors()
        self.db.add(SourceRule(id='reserve-blocked-brand', source_id='outfit-merchant',
            rule_type='blocked_brand', rule_value='Blocked brand', is_active=1))
        changes = [dict(available_sizes=['XL']), dict(feed_raw_json={}),
            dict(available_sizes_detailed=[{'raw_size': '38', 'availability': 'unavailable'}]),
            dict(gender_target='menswear'), dict(brand='Blocked brand'),
            dict(is_available=0, availability_status='out_of_stock'),
            dict(last_seen_in_feed_at=datetime.now(timezone.utc) - timedelta(days=4)),
            dict(image_url='http://127.0.0.1/private.jpg'), dict(product_url=''),
            dict(title='Лоферы для девочек'), dict(currency='USD'),
            dict(title='Кеды женские повседневные')]
        bad = set()
        for index, change in enumerate(changes):
            pid = f'a-invalid-cheap-{index:02}'
            bad.add(pid)
            self.db.add(Product(**self.fields(pid, 'shoes', price=Decimal('300'), **change)))
        self.db.commit()
        buckets = self.buckets(scenario='office')
        self.assertTrue(bad.isdisjoint({row.product.id for rows in buckets.values() for row in rows}))
        self.assertEqual({row.product.id for row in buckets['shoes'] if row.product.id.startswith('good-')},
            {f'good-shoes-{i}' for i in range(3)})
        self.db.close()
        self.assert_affordable_batch(outfit_session.build_outfits_response(self.engine,
            self.user_id, count=3, scenario='office'))

    def test_warm_legacy_refresh_reserves_cheap_choices_even_when_scored_roles_are_full(self):
        self.distractors()
        self.db.execute(delete(ActivationSession))
        self.db.commit()
        for slot, rows in self.buckets().items():
            if slot != 'one_piece':
                self.assertLessEqual(len(rows), 10)
                self.assertLessEqual({f'good-{slot}-{index}' for index in range(3)},
                    {row.product.id for row in rows})
        self.db.close()
        self.assert_affordable_batch(outfit_session.build_outfits_response(self.engine, self.user_id, count=3))

    def test_history_aliases_cannot_occupy_the_cheap_reserve_before_late_rejection(self):
        self.distractors()
        hidden = Product(**self.fields('historical-hidden', 'top', is_available=0,
            availability_status='out_of_stock'))
        self.db.add(hidden)
        aliases = set()
        for role in ('top', 'bottom', 'shoes'):
            for index in range(3):
                pid = f'cheaper-history-alias-{role}-{index}'
                aliases.add(pid)
                self.db.add(Product(**self.fields(pid, role, price=Decimal('300'),
                    image_url=hidden.image_url)))
        self.db.commit()
        rows = outfits._build_slot_candidates(self.db, self.user, scenario='daily',
            fit=self.fit, excluded=set(), skipped_ids=set(), per_slot=10,
            excluded_keys=product_identity_keys(hidden))
        selected = {row.product.id for bucket in rows.values() for row in bucket}
        self.assertTrue(selected.isdisjoint(aliases))
        self.assertLessEqual(self.good, selected)
        combinations = outfits._build_combinations(rows, scenario='daily', fit=self.fit, limit=3)
        self.assertTrue(combinations, 'History aliases cannot hide a distinct affordable purchase')

    def test_legacy_price_reserve_reaches_exact_shoes_after_six_thousand_wrong_sizes(self):
        self.db.execute(delete(ActivationSession))
        self.db.execute(insert(Product), [self.fields(f'newer-wrong-shoes-{index:04}', 'shoes',
            available_sizes=['39'], price=Decimal('300')) for index in range(6001)])
        self.db.commit()
        products = outfits._catalog_slot_candidates(self.db, self.user, slots={'shoes'},
            scenario='daily', excluded=set(), existing_ids=set(), limit=30, affordable_reserve=3)
        self.assertEqual({p.id for p in products}, {f'good-shoes-{i}' for i in range(3)})

    def test_large_stream_captures_finalist_evaluation_once_without_per_item_sql(self):
        self.distractors(300)
        evaluations, statements = Counter(), []
        original = AffinityScorer.evaluate

        def evaluate(scorer, product):
            evaluations[product.id] += 1
            return original(scorer, product)

        def record(_connection, _cursor, sql, *_unused):
            statements.append(sql)

        event.listen(self.engine, 'before_cursor_execute', record)
        try:
            with patch.object(AffinityScorer, 'evaluate', new=evaluate):
                rows = self.buckets()
        finally:
            event.remove(self.engine, 'before_cursor_execute', record)
        self.assertGreater(len(evaluations), 512)
        self.assertEqual(set(evaluations.values()), {1})
        self.assertLess(len(statements), 45, 'Size evidence must hydrate in batches, not one query per SKU')
        self.assertLessEqual(self.good, {row.product.id for bucket in rows.values() for row in bucket})


if __name__ == '__main__':
    unittest.main()
