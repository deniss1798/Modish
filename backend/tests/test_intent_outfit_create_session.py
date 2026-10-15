"""Outfit creation keeps complete, exact purchase facts across split phases."""
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine, delete, event, func, select
from sqlalchemy.orm import Session

from app.db import Base
from app.models import ActivationSession, MetricEvent, Outfit, Product, ProductSource, ShoppingIntent, User
from app.services import activation_service as activation, intent_session as intents, shopping_intent_service as service


OUTFIT_TEXT = 'Образ до 6000 без платья'
BAD_IDS = frozenset({'bad-size', 'bad-shoe-size', 'bad-shoe-unit', 'bad-gender',
    'bad-stock', 'bad-image', 'bad-price', 'bad-total'})


def seed_outfit_catalogue(db, *, user_id='outfit-create-owner'):
    """Fixed SKU labels are independent of implementation eligibility output."""
    user = User(id=user_id, email=user_id + '@example.test', password_hash='unused')
    db.add(user)
    db.flush()
    session = activation.configure(db, user, gender_target='womenswear', age_group='25-34',
        height=170, clothing_size='M', clothing_size_system='LETTER', shoe_size='38',
        shoe_size_system='EU', styles=['casual', 'minimalism'], price_segment='mass')
    session.completed_at = datetime.now(timezone.utc)
    db.add(ProductSource(id='outfit-merchant', code='baon', name='BAON', network='direct'))
    db.flush()
    now = datetime.now(timezone.utc)

    def product(sku, category='рубашки', title='Женская рубашка хлопковая', **changes):
        shoe = category == 'обувь'
        fields = dict(id=sku, external_id=sku, source='baon', source_id='outfit-merchant',
            title=title, brand='Synthetic outfit brand', category=category, price=Decimal('1200.25'), currency='RUB',
            gender_target='womenswear', colors=['black'], style_tags=['casual', 'minimalism'],
            available_sizes=['38' if shoe else 'M'],
            feed_raw_json={'size_system': 'EU' if shoe else 'LETTER'},
            image_url=f'https://baon.ru/images/{sku}.jpg', product_url=f'https://baon.ru/products/{sku}',
            is_active=1, is_available=1, is_deleted_from_feed=0, availability_status='in_stock',
            last_seen_in_feed_at=now)
        fields.update(changes)
        db.add(Product(**fields))

    for index in range(3):
        product(f'good-top-{index}', price=Decimal('1200.25') + index * 100)
        product(f'good-bottom-{index}', 'брюки', 'Женские брюки прямые',
            price=Decimal('1800.50') + index * 100)
        product(f'good-shoes-{index}', 'обувь', 'Женские лоферы кожаные',
            price=Decimal('2000.25') + index * 100)
    product('bad-size', available_sizes=['M/L'])
    product('bad-shoe-size', 'обувь', 'Женские лоферы', available_sizes=['39'])
    product('bad-shoe-unit', 'обувь', 'Женские лоферы', feed_raw_json={})
    product('bad-gender', gender_target='menswear')
    product('bad-stock', last_seen_in_feed_at=now - timedelta(days=4))
    product('bad-image', image_url='')
    product('bad-price', price=Decimal('6000.01'))
    product('bad-total', 'обувь', 'Женские лоферы', price=Decimal('5000.99'))
    db.commit()
    return user_id


def metric_counts(db, intent_id):
    return Counter(row.name for row in db.scalars(select(MetricEvent))
        if (row.meta_json or {}).get('intent_id') == intent_id)


def assert_full_outfits(case, response, *, nonempty=True, forbidden=(), maximum=6000):
    case.assertEqual(response['products'], [])
    outfits = response['outfits']
    if nonempty:
        case.assertTrue(outfits, 'An independent positive catalogue must produce complete outfits')
    for outfit in outfits:
        case.assertEqual(set(outfit['items']), {'top', 'bottom', 'shoes'})
        case.assertEqual(set(outfit['products']), {'top', 'bottom', 'shoes'})
        case.assertEqual(set(outfit['size_matches']), {'top', 'bottom', 'shoes'})
        case.assertEqual(len(set(outfit['items'].values())), 3)
        case.assertTrue(set(outfit['items'].values()).isdisjoint(BAD_IDS | set(forbidden)))
        exact = sum((Decimal(str(product['price'])) for product in outfit['products'].values()), Decimal(0))
        case.assertEqual(Decimal(str(outfit['total_price'])), exact)
        case.assertLessEqual(exact, Decimal(maximum))
        case.assertEqual(outfit['confidence']['fit'], 'confirmed')
        for slot, product in outfit['products'].items():
            case.assertEqual(product['gender_target'], 'womenswear')
            case.assertEqual(product['currency'], 'RUB')
            case.assertTrue(product['is_available'])
            expected_size, expected_system = ('38', 'EU') if slot == 'shoes' else ('M', 'LETTER')
            case.assertEqual(outfit['size_matches'][slot]['normalized_size'], expected_size)
            case.assertEqual(outfit['size_matches'][slot]['size_system'], expected_system)
            case.assertEqual(outfit['size_matches'][slot]['availability'], 'available')


class IntentOutfitCreateSessionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        with Session(self.engine, autoflush=False) as db:
            self.user_id = seed_outfit_catalogue(db)
        self.key = 'outfit-create-stable-key'

    def build(self, **changes):
        return intents.build_intent_response(self.engine, self.user_id, text=changes.get('text', OUTFIT_TEXT),
            request_id=changes.get('request_id', self.key))

    def assert_once(self, response):
        with Session(self.engine) as db:
            counts = metric_counts(db, response['id'])
            for name in ('intent_created', 'intent_parsed', 'intent_results_generated', 'availability_filtered', 'size_filtered'):
                self.assertEqual(counts[name], 1, (name, counts))
            self.assertEqual(counts['outfit_generated'], len(response['outfits']))

    def test_outfit_rank_is_read_only_and_matches_protected_service_positive_results(self):
        with Session(self.engine, autoflush=False) as db:
            user = db.get(User, self.user_id)
            intent = service.create_intent(db, user, text=OUTFIT_TEXT, request_id='legacy-parity-request-key')
            expected = service.intent_response(db, user, intent, remember=True)
            expected_items = {tuple(sorted(outfit['items'].items())) for outfit in expected['outfits']}
            self.assertTrue(expected_items)
            db.rollback()
        original, statements = intents.build_intent_outfits, []

        def rank(db, *args, **kwargs):
            self.assertTrue(db.info['recommendation_read_only'])
            self.assertNotIn('_recommendation_owner_mutations', db.info)

            def record(connection, cursor, sql, *args):
                statements.append(sql.strip().split()[0].upper())

            event.listen(self.engine, 'before_cursor_execute', record)
            try:
                return original(db, *args, **kwargs)
            finally:
                event.remove(self.engine, 'before_cursor_execute', record)

        with patch.object(intents, 'build_intent_outfits', side_effect=rank) as ranking:
            result = self.build()
        self.assertEqual(ranking.call_count, 1)
        self.assertEqual(set(statements), {'SELECT'})
        assert_full_outfits(self, result)
        self.assertEqual({tuple(sorted(outfit['items'].items())) for outfit in result['outfits']}, expected_items)
        self.assert_once(result)

    def _changed_finalist_reranks(self, *, stock):
        original, changed = intents._publish, []

        def publish(sessions, user_id, proposal):
            if not changed:
                changed.append(dict(proposal.outfits[0])['shoes'])
                with sessions() as db:
                    row = db.get(Product, changed[0])
                    if stock:
                        row.is_available, row.availability_status = 0, 'out_of_stock'
                    else:
                        row.price = Decimal('6000.99')
                    db.commit()
            return original(sessions, user_id, proposal)

        with patch.object(intents, '_publish', side_effect=publish) as publishing:
            result = self.build()
        self.assertEqual(publishing.call_count, 2)
        assert_full_outfits(self, result, forbidden=changed)
        self.assert_once(result)

    def test_stock_change_after_prepare_reranks_once_without_stale_outfit(self):
        self._changed_finalist_reranks(stock=True)

    def test_price_change_after_prepare_reranks_once_without_stale_outfit(self):
        self._changed_finalist_reranks(stock=False)

    def test_repeated_conflicts_publish_no_partial_outfits_or_metrics(self):
        original = intents._publish

        def publish(sessions, user_id, proposal):
            with sessions() as db:
                db.get(Product, proposal.finalists[0].product_id).price += Decimal('0.01')
                db.commit()
            return original(sessions, user_id, proposal)

        with patch.object(intents, '_publish', side_effect=publish) as publishing, self.assertRaises(HTTPException) as caught:
            self.build()
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(publishing.call_count, 2)
        with Session(self.engine) as db:
            row = db.scalar(select(ShoppingIntent))
            self.assertNotIn('result_version', row.context_json)
            self.assertFalse(row.context_json.get('outfits'))
            counts = metric_counts(db, row.id)
            self.assertEqual(counts['intent_results_generated'], 0)
            self.assertEqual(counts['outfit_generated'], 0)
            self.assertEqual(db.scalar(select(func.count()).select_from(Outfit)), 0)

    def test_missing_mandatory_shoes_never_falls_back_to_partial_outfit(self):
        with Session(self.engine) as db:
            db.execute(delete(Product).where(Product.category == 'обувь'))
            db.commit()
        result = self.build()
        assert_full_outfits(self, result, nonempty=False)
        self.assertEqual(result['outfits'], [])
        self.assertIn('полный комплект', result['message'])
        self.assert_once(result)

    def test_total_budget_preserves_one_kopeck_boundary_without_relaxation(self):
        with Session(self.engine) as db:
            for product in db.scalars(select(Product).where(Product.id.like('good-%'))):
                product.price = {'рубашки': Decimal('1000'), 'брюки': Decimal('2000'), 'обувь': Decimal('3000.01')}[product.category]
            db.commit()
        over = self.build()
        self.assertEqual(over['outfits'], [])
        with Session(self.engine) as db:
            db.get(Product, 'good-shoes-0').price = Decimal('3000')
            db.commit()
        exact = self.build(request_id='outfit-exact-budget-key')
        assert_full_outfits(self, exact)
        self.assertTrue(all(Decimal(str(outfit['total_price'])) == Decimal('6000') for outfit in exact['outfits']))
        self.assert_once(exact)

    def test_same_key_replay_keeps_issued_outfits_without_rescoring_or_duplicate_metrics(self):
        first = self.build()
        assert_full_outfits(self, first)
        with patch.object(intents, 'build_intent_outfits', side_effect=AssertionError('POST replay must not compose')), \
                patch.object(service, 'build_intent_outfits', side_effect=AssertionError('GET replay must not compose')):
            replay = self.build()
            with Session(self.engine) as db:
                user = db.get(User, self.user_id)
                read = service.issued_intent_response(db, user, service.get_intent(db, user, first['id']))
        self.assertEqual(replay, first)
        self.assertEqual(read, first)
        self.assert_once(first)
