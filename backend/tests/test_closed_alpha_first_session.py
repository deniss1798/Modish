"""First-session admission against fixed synthetic SKU labels, not gate output.

Every positive/negative is labelled while constructing the catalogue. Expected
sets never call an eligibility, URL, size, gender or classification helper.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest

from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.products import _feed_scored_items
from app.db import Base
from app.models import ActivationExposure, Product, ProductImpression, ProductSource, RecommendationEventV2, SourceRule, User
from app.schemas.product_event import EventRequest
from app.services import activation_service as activation
from app.services.catalog_filters import CatalogFilters
from app.services.product_event_service import record_product_event


_CATEGORIES = (
    ('рубашки', 'Женская рубашка хлопковая'),
    ('брюки', 'Женские брюки прямые'),
    ('платья', 'Женское платье однотонное'),
    ('юбки', 'Женская юбка миди'),
    ('футболки', 'Женская футболка базовая'),
    ('обувь', 'Женские лоферы кожаные'),
)


class ClosedAlphaFirstSessionTests(unittest.TestCase):
    @contextmanager
    def scenario(self, size='M', segment='mass', maximum=10000):
        engine = create_engine('sqlite://')
        Base.metadata.create_all(engine)
        try:
            with Session(engine, autoflush=False) as db:
                self.db = db
                self.size, self.maximum = size, Decimal(maximum)
                self.user = User(id='first-session-user', email='first-session@example.test', password_hash='unused')
                db.add(self.user)
                db.flush()
                self.session = activation.configure(db, self.user, gender_target='womenswear', age_group='25-34',
                    height=170, clothing_size=size, clothing_size_system='LETTER', shoe_size='38',
                    shoe_size_system='EU', styles=['casual', 'minimalism'], price_segment=segment)
                self.good, self.bad = set(), set()
                self.rows = {}
                yield
        finally:
            engine.dispose()

    def product(self, sku, category='рубашки', title='Женская рубашка хлопковая', **changes):
        shoe = category == 'обувь'
        fields = dict(id=sku, external_id=sku, source='baon', brand='One affordable brand',
            title=title, category=category, price=Decimal('799.50'), currency='RUB',
            gender_target='womenswear', colors=['black'], style_tags=['casual', 'minimalism', 'elegant'],
            available_sizes=['38' if shoe else self.size], feed_raw_json={'size_system': 'EU' if shoe else 'LETTER'},
            image_url=f'https://baon.ru/images/{sku}.jpg', product_url=f'https://baon.ru/products/{sku}',
            is_active=1, is_available=1, availability_status='in_stock', is_deleted_from_feed=0,
            last_seen_in_feed_at=datetime.now(timezone.utc))
        fields.update(changes)
        row = Product(**fields)
        self.db.add(row)
        self.rows[sku] = row
        return row

    def positive_catalogue(self, per_category=12):
        for category_index, (category, title) in enumerate(_CATEGORIES):
            for index in range(per_category):
                sku = f'z-good-{category_index}-{index:03}'
                self.product(sku, category, title)
                self.good.add(sku)
        # Fixed RUB boundary examples are deliberately positive, including cents.
        for sku, price in (('z-boundary-floor', Decimal('300.00')),
                           ('z-boundary-max', self.maximum),
                           ('z-boundary-cents', Decimal('1299.99'))):
            self.product(sku, price=price)
            self.good.add(sku)
        self.db.flush()

    def negative_catalogue(self):
        variants = {
            'opposite-gender': {'gender_target': 'menswear'},
            'unisex-not-selected': {'gender_target': 'unisex'},
            'unknown-gender': {'gender_target': None},
            'contradictory-mens-title': {'title': 'Мужская рубашка хлопковая'},
            'wrong-size': {'available_sizes': ['XXXL']},
            'missing-size': {'available_sizes': []},
            'ambiguous-size': {'available_sizes': ['M/L'], 'feed_raw_json': {}},
            'budget-kopecks': {'price': self.maximum + Decimal('0.99')},
            'below-minimum': {'price': Decimal('299.99')},
            'zero-price': {'price': Decimal('0')},
            'foreign-currency': {'currency': 'USD'},
            'empty-currency': {'currency': ''},
            'kids-title': {'title': 'Рубашка для девочек'},
            'kids-metadata': {'merchant_category': 'Kids clothing'},
            'kids-params': {'raw_params_json': {'age_group': 'children'}},
            'nonfashion': {'title': 'Крем для лица'},
            'nonfashion-home': {'title': 'Подушка декоративная'},
            'sports': {'title': 'Топ для фитнеса'},
            'no-image': {'image_url': ''},
            'private-image': {'image_url': 'https://127.0.0.1/image.jpg'},
            'bad-port-image': {'image_url': 'https://shop.example:bad/image.jpg'},
            'non-web-image': {'image_url': 'data:image/png;base64,SYNTHETIC'},
            'no-outbound': {'product_url': '', 'original_url': None, 'affiliate_url': None},
            'private-outbound': {'product_url': 'http://localhost/product'},
            'trailing-dot-outbound': {'product_url': 'https://localhost./p'},
            'non-web-outbound': {'product_url': 'javascript:void(0)'},
            'inactive': {'is_active': 0},
            'out-of-stock': {'is_available': 0, 'availability_status': 'out_of_stock'},
            'deleted': {'is_deleted_from_feed': 1},
            'stale': {'last_seen_in_feed_at': datetime.now(timezone.utc) - timedelta(days=4)},
            'excluded-source': {'source': 'vipavenue'},
        }
        for reason, changes in variants.items():
            sku = 'a-bad-' + reason
            self.product(sku, **changes)
            self.bad.add(sku)
        for reason, changes in (
                ('wrong-shoe-size', {'available_sizes': ['39']}),
                ('wrong-shoe-unit', {'available_sizes': ['M'], 'feed_raw_json': {'size_system': 'LETTER'}})):
            sku = 'a-bad-' + reason
            self.product(sku, 'обувь', 'Женские лоферы кожаные', **changes)
            self.bad.add(sku)
        # Invalid early SQL rows must not use the category's fifty-card capacity.
        for index in range(60):
            sku = f'a-bad-size-prefix-{index:03}'
            self.product(sku, available_sizes=['XXXL'])
            self.bad.add(sku)
        self.db.flush()

    def assert_oracle(self, ids, *, nonempty=True):
        ids = list(ids)
        if nonempty:
            self.assertTrue(ids, 'A positive oracle must produce real results')
        self.assertEqual(len(ids), len(set(ids)))
        self.assertLessEqual(set(ids), self.good)
        self.assertTrue(set(ids).isdisjoint(self.bad))

    def warm(self):
        deck = activation.swipe_deck(self.db, self.user, self.session)
        self.assertEqual(len(deck['products']), 20)
        self.assert_oracle(row['id'] for row in deck['products'])
        liked = {row['id'] for row in deck['products'][:15]}
        for sku in sorted(liked):
            activation.swipe_feedback(self.db, self.user, self.session, sku, 'like')
        self.db.commit()
        events = self.db.scalars(select(RecommendationEventV2).where(
            RecommendationEventV2.user_id == self.user.id)).all()
        self.assertEqual({event.product_id for event in events}, liked)
        self.assertTrue(all(event.meta_json['surface'] == 'style_swipe' for event in events))
        result = activation.complete_wow(self.db, self.user, self.session)
        self.assertTrue(result['completed'])
        self.assertTrue(result['first_session_feed'])
        for outfit in result['outfits']:
            self.assert_oracle(outfit['items'].values())
        self.db.commit()
        return liked

    def test_three_profiles_keep_all_hard_gates_through_first_twenty_feed_cards(self):
        for size, segment, maximum in (('XS', 'economy', 4000), ('M', 'mass', 10000), ('L', 'mid', 25000)):
            with self.subTest(size=size, segment=segment), self.scenario(size, segment, maximum):
                self.positive_catalogue()
                self.negative_catalogue()
                # Exercise both exact RUB boundaries as real admissions, even
                # if ranking later chooses other eligible cards for its twenty.
                pool = activation.candidate_pool(self.db, self.user, self.session)
                self.assertEqual({row.id for row in pool}, self.good)
                liked = self.warm()
                feed = _feed_scored_items(self.db, self.user, limit=20)
                self.assertEqual(len(feed), 20)
                self.assert_oracle(row['product']['id'] for row in feed)
                self.assertTrue({row['product']['id'] for row in feed}.isdisjoint(liked))
                for row in feed:
                    self.assertGreaterEqual(Decimal(str(row['product']['price'])), Decimal('300'))
                    self.assertLessEqual(Decimal(str(row['product']['price'])), self.maximum)
                    record_product_event(self.db, self.user, EventRequest(product_id=row['product']['id'],
                        event_type='view', meta={'surface': 'feed'}))
                impressions = self.db.scalars(select(ProductImpression)).all()
                self.assertEqual(len(impressions), 20)
                self.assertTrue(all(row.ranking_meta_json['ranking_algorithm'] == 'first_session_feed' for row in impressions))
                self.assertEqual(len(self.db.scalars(select(ActivationExposure)).all()), 20)

    def test_cached_deck_rechecks_changed_quality_and_refill_never_uses_bad_skus(self):
        with self.scenario():
            self.positive_catalogue()
            self.negative_catalogue()
            first = activation.swipe_deck(self.db, self.user, self.session)
            self.assertEqual(len(first['products']), 20)
            mutations = ({'available_sizes': ['XXXL']}, {'gender_target': 'menswear'},
                {'price': Decimal('10000.99')}, {'title': 'Рубашка для девочек'},
                {'title': 'Крем для лица'}, {'image_url': 'https://127.0.0.1/image.jpg'},
                {'product_url': 'http://localhost/product'})
            for card, changes in zip(first['products'], mutations):
                sku = card['id']
                for field, value in changes.items():
                    setattr(self.rows[sku], field, value)
                self.good.remove(sku)
                self.bad.add(sku)
            self.db.flush()
            refilled = activation.swipe_deck(self.db, self.user, self.session)
            self.assertEqual(len(refilled['products']), 20)
            self.assert_oracle(row['id'] for row in refilled['products'])

    def test_short_swipe_inventory_and_exhaustion_return_fewer_without_relaxing(self):
        with self.scenario():
            self.negative_catalogue()
            self.positive_catalogue(per_category=1)
            # All scarce products share a brand: fallback may relax diversity,
            # but cannot invent a fifteenth admissible card.
            deck = activation.swipe_deck(self.db, self.user, self.session)
            self.assertLess(len(deck['products']), 15)
            self.assert_oracle(row['id'] for row in deck['products'])
            for card in deck['products']:
                activation.swipe_feedback(self.db, self.user, self.session, card['id'], 'unavailable')
            rest = activation.swipe_deck(self.db, self.user, self.session)
            self.assert_oracle((row['id'] for row in rest['products']), nonempty=False)
            for card in rest['products']:
                activation.swipe_feedback(self.db, self.user, self.session, card['id'], 'unavailable')
            self.assertEqual(activation.swipe_deck(self.db, self.user, self.session)['products'], [])
            self.assertEqual(activation.rated_count(self.session), 0)
            with self.assertRaises(HTTPException):
                activation.complete_wow(self.db, self.user, self.session)

    def test_first_feed_shortage_and_filtered_fallback_keep_oracle_then_empty(self):
        with self.scenario():
            self.positive_catalogue()
            self.negative_catalogue()
            liked = self.warm()
            liked_categories = {self.rows[sku].category for sku in liked}
            keep = set()
            for index, (category, _) in enumerate(_CATEGORIES):
                if category not in liked_categories:
                    continue
                keep.add(next(sku for sku in sorted(self.good - liked) if sku.startswith(f'z-good-{index}-')))
            for sku in self.good - keep:
                self.rows[sku].is_available = 0
            self.db.flush()
            feed = _feed_scored_items(self.db, self.user, limit=20)
            self.assertEqual({row['product']['id'] for row in feed}, keep)
            self.assert_oracle(row['product']['id'] for row in feed)
            top = next(sku for sku in keep if sku.startswith('z-good-0-'))
            filtered = _feed_scored_items(self.db, self.user, limit=20, filters=CatalogFilters(categories=('tops',)))
            self.assertEqual([row['product']['id'] for row in filtered], [top])
            empty = _feed_scored_items(self.db, self.user, limit=20, exclude_ids=list(keep))
            self.assertEqual(empty, [])

    def test_stale_like_or_dislike_cannot_train_or_count_but_unavailable_remains_valid(self):
        with self.scenario():
            self.positive_catalogue()
            deck = activation.swipe_deck(self.db, self.user, self.session)
            sku = deck['products'][0]['id']
            self.rows[sku].price = Decimal('10000.99')
            self.db.flush()
            for reaction in ('like', 'dislike'):
                with self.subTest(reaction=reaction), self.assertRaises(HTTPException) as error:
                    activation.swipe_feedback(self.db, self.user, self.session, sku, reaction)
                self.assertEqual(error.exception.status_code, 409)
                self.assertEqual(activation.rated_count(self.session), 0)
                self.assertEqual(self.db.scalars(select(RecommendationEventV2)).all(), [])
            activation.swipe_feedback(self.db, self.user, self.session, sku, 'unavailable')
            self.assertEqual(activation.rated_count(self.session), 0)
            self.assertEqual(self.db.scalars(select(RecommendationEventV2)).all(), [])
            self.assertNotIn(sku, {row['id'] for row in activation.swipe_deck(self.db, self.user, self.session)['products']})

    def test_repeated_accepted_reaction_remains_idempotent_after_stock_changes(self):
        with self.scenario():
            self.positive_catalogue()
            deck = activation.swipe_deck(self.db, self.user, self.session)
            sku = deck['products'][0]['id']
            activation.swipe_feedback(self.db, self.user, self.session, sku, 'like')
            self.rows[sku].is_available = 0
            self.db.flush()
            result = activation.swipe_feedback(self.db, self.user, self.session, sku, 'like')
            self.assertEqual(result['rated'], 1)
            self.assertEqual(len(self.db.scalars(select(RecommendationEventV2)).all()), 1)

    def test_missing_currency_never_inherits_rubles_during_candidate_or_deck_reads(self):
        for value in (None, ''):
            with self.subTest(currency=value), self.scenario():
                self.positive_catalogue(per_category=1)
                sku = 'a-bad-unknown-currency'
                unknown = self.product(sku)
                self.bad.add(sku)
                self.db.flush()
                # An INSERT default may replace None with RUB. Mutate only after
                # flush so this tests the actual unknown value at admission;
                # autoflush=False also covers pending merchant updates.
                unknown.currency = value
                pool = activation.candidate_pool(self.db, self.user, self.session)
                self.assertEqual({row.id for row in pool}, self.good)
                self.assert_oracle(row.id for row in pool)
                deck = activation.swipe_deck(self.db, self.user, self.session)
                self.assert_oracle(row['id'] for row in deck['products'])

    def test_cached_swipe_honors_new_source_rule_before_refilling(self):
        with self.scenario():
            source = ProductSource(id='source-rule', code='oracle-source', name='Synthetic source', network='direct')
            self.db.add(source)
            self.positive_catalogue()
            for sku in self.good:
                self.rows[sku].source_id = source.id
            self.db.flush()
            deck = activation.swipe_deck(self.db, self.user, self.session)
            self.assertEqual(len(deck['products']), 20)
            self.db.add(SourceRule(id='rule-block-shirts', source_id=source.id, rule_type='blocked_category',
                rule_value='рубашки', is_active=1))
            rejected = {sku for sku in self.good if self.rows[sku].category == 'рубашки'}
            self.good -= rejected
            self.bad |= rejected
            self.db.flush()
            stale = next(card['id'] for card in deck['products'] if card['id'] in rejected)
            with self.assertRaises(HTTPException) as error:
                activation.swipe_feedback(self.db, self.user, self.session, stale, 'like')
            self.assertEqual(error.exception.status_code, 409)
            self.assertEqual(self.db.scalars(select(RecommendationEventV2)).all(), [])
            fresh = activation.swipe_deck(self.db, self.user, self.session)
            self.assertEqual(len(fresh['products']), 20)
            self.assert_oracle(row['id'] for row in fresh['products'])
