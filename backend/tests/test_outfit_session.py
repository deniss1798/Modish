"""Standalone outfit refresh ranks read-only and publishes one complete batch."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine, delete, event, func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm import sessionmaker

from app.api import outfits as outfit_api
from app.db import Base
from app.models import (ActivationSession, FitProfile, MetricEvent, Outfit, Product,
    RecommendationCandidatePool, User, UserRecommendationCache)
from app.schemas.product_event import EventRequest
from app.services import outfit_session as outfits
from app.services.product_event_service import record_product_event
from app.services.recommendation_budget import RecommendationRankingTimeout
from app.services.recommendation_revision import RecommendationSnapshotChanged
from app.services.product_identity import product_identity_keys
import test_intent_outfit_create_session as fixtures


def seed_outfit_refresh_catalogue(db):
    user_id = fixtures.seed_outfit_catalogue(db)
    db.scalar(select(FitProfile).where(FitProfile.user_id == user_id)).budget_max = 6000
    db.commit()
    return user_id


def assert_complete_batch(case, response, *, slots=('top', 'bottom', 'shoes'), forbidden=()):
    case.assertTrue(response, 'The independent positive catalogue must produce a complete batch')
    issued = set()
    for outfit in response:
        case.assertEqual(set(outfit['items']), set(slots))
        case.assertEqual(set(outfit['products']), set(slots))
        ids = set(outfit['items'].values())
        case.assertEqual(len(ids), len(slots))
        case.assertTrue(ids.isdisjoint(fixtures.BAD_IDS | set(forbidden)))
        case.assertTrue(ids.isdisjoint(issued), 'A new batch must diversify its garments')
        issued.update(ids)
        exact = sum((Decimal(str(product['price'])) for product in outfit['products'].values()), Decimal(0))
        case.assertEqual(Decimal(str(outfit['total_price'])), exact)
        case.assertLessEqual(exact, Decimal('6000'))
        for slot, product in outfit['products'].items():
            case.assertEqual(product['gender_target'], 'womenswear')
            case.assertEqual(product['currency'], 'RUB')
            case.assertTrue(product['is_available'])
            case.assertEqual(product['available_sizes'], ['38' if slot == 'shoes' else 'M'])


class OutfitSessionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        with Session(self.engine, autoflush=False) as db:
            self.user_id = seed_outfit_refresh_catalogue(db)

    def build(self, *, count=2, allow_clothing_base=True):
        return outfits.build_outfits_response(self.engine, self.user_id, count=count, scenario='daily',
            allow_clothing_base=allow_clothing_base)

    def test_deadline_after_rank_does_not_publish_any_outfit_history_cache_or_revision(self):
        with Session(self.engine) as db:
            before_revision = db.get(User, self.user_id).recommendation_revision
        with patch.object(outfits.engine, 'prepare_outfit_batch', wraps=outfits.engine.prepare_outfit_batch) as ranking, \
                patch.object(outfits, 'check_ranking_budget', side_effect=RecommendationRankingTimeout()), \
                self.assertRaises(HTTPException) as caught:
            self.build()
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(ranking.call_count, 1)
        with Session(self.engine) as db:
            self.assertEqual(db.get(User, self.user_id).recommendation_revision, before_revision)
            for model in (Outfit, MetricEvent, RecommendationCandidatePool, UserRecommendationCache):
                self.assertEqual(db.scalar(select(func.count()).select_from(model)), 0, model.__name__)

    def _positive_parity(self, *, slots=('top', 'bottom', 'shoes')):
        with Session(self.engine, autoflush=False) as db:
            rows = outfits.engine.generate_outfits_v2(db, db.get(User, self.user_id), count=2, scenario='daily')
            expected = {tuple(sorted(row.items_json.items())): row.total_price for row in rows}
            self.assertTrue(expected)
            db.rollback()
        original, statements = outfits.engine.prepare_outfit_batch, []

        def rank(db, *args, **kwargs):
            self.assertTrue(db.info['recommendation_read_only'])
            self.assertNotIn('_recommendation_owner_mutations', db.info)

            def record(connection, cursor, sql, *unused):
                statements.append(sql.strip().split()[0].upper())

            event.listen(self.engine, 'before_cursor_execute', record)
            try:
                return original(db, *args, **kwargs)
            finally:
                event.remove(self.engine, 'before_cursor_execute', record)

        with patch.object(outfits.engine, 'prepare_outfit_batch', side_effect=rank) as ranking:
            result = self.build()
        self.assertEqual(ranking.call_count, 1)
        self.assertEqual(set(statements), {'SELECT'})
        assert_complete_batch(self, result, slots=slots)
        self.assertEqual({tuple(sorted(row['items'].items())): Decimal(str(row['total_price'])) for row in result}, expected)
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Outfit)), len(result))
            self.assertEqual(db.scalar(select(func.count()).select_from(MetricEvent).where(
                MetricEvent.name == 'outfit_rotation:daily')), 1)

    def test_three_role_positive_parity_ranks_without_writes(self):
        self._positive_parity()

    def test_one_piece_positive_parity_keeps_complete_two_role_outfits(self):
        with Session(self.engine) as db:
            db.execute(delete(Product).where(Product.category.in_(['рубашки', 'брюки'])))
            for index in range(3):
                db.add(Product(id=f'good-dress-{index}', external_id=f'good-dress-{index}',
                    source='baon', source_id='outfit-merchant', title='Женское платье повседневное',
                    brand='Synthetic dress brand', category='платья', price=Decimal('1500.25') + index * 100,
                    currency='RUB', gender_target='womenswear', colors=['black'], style_tags=['casual', 'minimalism'],
                    available_sizes=['M'], feed_raw_json={'size_system': 'LETTER'},
                    image_url=f'https://baon.ru/images/dress-{index}.jpg',
                    product_url=f'https://baon.ru/products/dress-{index}', is_active=1, is_available=1,
                    is_deleted_from_feed=0, availability_status='in_stock', last_seen_in_feed_at=datetime.now(timezone.utc)))
            db.commit()
        self._positive_parity(slots=('one_piece', 'shoes'))

    def _changed_finalist(self, kind):
        original, changed = outfits._publish, []

        def publish(sessions, user_id, proposal, **kwargs):
            if not changed:
                changed.append(next(item.product_id for item in proposal.choices[0].items if item.slot == 'shoes'))
                with sessions() as db:
                    product = db.get(Product, changed[0])
                    if kind == 'stock':
                        product.is_available, product.availability_status = 0, 'out_of_stock'
                        db.commit()
                    elif kind == 'price':
                        product.price = Decimal('6000.99')
                        db.commit()
                    else:
                        record_product_event(db, db.get(User, user_id), EventRequest(
                            product_id=product.id, event_type='dislike', meta={'surface': 'feed'}))
            return original(sessions, user_id, proposal, **kwargs)

        with patch.object(outfits, '_publish', side_effect=publish) as publishing:
            result = self.build(count=1)
        self.assertEqual(publishing.call_count, 2)
        assert_complete_batch(self, result, forbidden=changed)
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Outfit)), 1)
            self.assertEqual(db.scalar(select(func.count()).select_from(MetricEvent).where(
                MetricEvent.name == 'outfit_rotation:daily')), 1)

    def test_price_change_after_prepare_reranks_once_without_stale_sku(self):
        self._changed_finalist('price')

    def test_stock_change_after_prepare_reranks_once_without_stale_sku(self):
        self._changed_finalist('stock')

    def test_dislike_after_prepare_reranks_once_without_disliked_sku(self):
        self._changed_finalist('dislike')

    def test_alias_swap_across_prior_outfits_cannot_hide_behind_same_global_identity_union(self):
        sessions = sessionmaker(bind=self.engine, autoflush=False)
        initial = outfits._prepare(sessions, self.user_id, count=1, scenario='daily')
        chosen = {item.slot: item.product_id for item in initial.choices[0].items}
        with sessions() as db:
            db.execute(delete(Product).where(Product.id.like('good-%'), Product.id.notin_(chosen.values())))
            a, b, c = (db.get(Product, pid) for pid in ('bad-size', 'bad-shoe-unit', 'bad-price'))
            a.image_url = db.get(Product, chosen['top']).image_url
            b.image_url = db.get(Product, chosen['bottom']).image_url
            db.add_all([
                Outfit(id='prior-alias-a', user_id=self.user_id, style_direction='daily',
                    items_json={'top': a.id, 'bottom': c.id}, total_price=0),
                Outfit(id='prior-alias-b', user_id=self.user_id, style_direction='daily',
                    items_json={'top': b.id}, total_price=0),
                Outfit(id='prior-shared-shoe', user_id=self.user_id, style_direction='daily',
                    items_json={'shoes': chosen['shoes']}, total_price=0),
            ])
            db.commit()
            before_union = set().union(*(product_identity_keys(p) for p in (a, b, c)))
        original, changed = outfits._publish, []

        def publish(sessions, user_id, proposal, **kwargs):
            if not changed:
                changed.append(proposal)
                with sessions() as db:
                    b, c = (db.get(Product, pid) for pid in ('bad-shoe-unit', 'bad-price'))
                    b.image_url, c.image_url = c.image_url, b.image_url
                    db.commit()
                    current = [db.get(Product, pid) for pid in ('bad-size', 'bad-shoe-unit', 'bad-price')]
                    self.assertEqual(set().union(*(product_identity_keys(p) for p in current)), before_union)
                    old_choice = [db.get(Product, item.product_id) for item in proposal.choices[0].items]
                    prior_a_keys = product_identity_keys(current[0]) | product_identity_keys(current[2])
                    self.assertGreaterEqual(sum(bool(product_identity_keys(p) & prior_a_keys) for p in old_choice), 2)
            return original(sessions, user_id, proposal, **kwargs)

        with patch.object(outfits, '_publish', side_effect=publish) as publishing:
            result = self.build(count=1)
        self.assertEqual(publishing.call_count, 2)
        self.assertEqual(result, [])
        with sessions() as db:
            self.assertEqual(set(db.scalars(select(Outfit.id))),
                {'prior-alias-a', 'prior-alias-b', 'prior-shared-shoe'})
            self.assertEqual(db.scalar(select(func.count()).select_from(MetricEvent)), 0)

    def test_strict_complete_budget_rejects_one_kopeck_over_and_accepts_exact_sum(self):
        with Session(self.engine) as db:
            for product in db.scalars(select(Product).where(Product.id.like('good-%'))):
                product.price = {'рубашки': Decimal('1000'), 'брюки': Decimal('2000'), 'обувь': Decimal('3000.01')}[product.category]
            db.commit()
        self.assertEqual(self.build(allow_clothing_base=False), [])
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Outfit)), 0)
            db.get(Product, 'good-shoes-0').price = Decimal('3000')
            db.commit()
        result = self.build(count=1)
        assert_complete_batch(self, result)
        self.assertEqual(Decimal(str(result[0]['total_price'])), Decimal('6000'))

    def test_two_late_conflicts_roll_back_rows_rotation_revision_and_legacy_cache(self):
        with Session(self.engine) as db:
            db.execute(delete(ActivationSession))
            old = Outfit(id='previous-unsaved-outfit', user_id=self.user_id, style_direction='daily',
                items_json={'top': 'good-top-0', 'bottom': 'good-bottom-0', 'shoes': 'good-shoes-0'},
                total_price=Decimal('5001'), is_saved=0)
            db.add(old)
            db.commit()
            before_revision = db.get(User, self.user_id).recommendation_revision
            old_items = deepcopy(old.items_json)
        observed = []

        def reject_after_flush(db, user, rows, **kwargs):
            self.assertTrue(rows)
            self.assertFalse(db.get(Outfit, 'previous-unsaved-outfit'))
            self.assertTrue(db.scalar(select(RecommendationCandidatePool)))
            self.assertTrue(db.scalar(select(UserRecommendationCache)))
            self.assertEqual(db.scalar(select(func.count()).select_from(MetricEvent).where(
                MetricEvent.name == 'outfit_rotation:daily')), 1)
            observed.append(tuple(row.id for row in rows))
            raise RecommendationSnapshotChanged()

        with patch('app.api.outfits.visible_outfits', side_effect=reject_after_flush), \
                patch.object(outfits, 'persist_recommendation_cache_ids', wraps=outfits.persist_recommendation_cache_ids) as cache, \
                self.assertRaises(HTTPException) as caught:
            self.build(count=1)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(len(observed), 2)
        self.assertEqual(cache.call_count, 2)
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Outfit)), 1)
            self.assertEqual(db.get(Outfit, 'previous-unsaved-outfit').items_json, old_items)
            self.assertEqual(db.get(User, self.user_id).recommendation_revision, before_revision)
            for model in (MetricEvent, RecommendationCandidatePool, UserRecommendationCache):
                self.assertEqual(db.scalar(select(func.count()).select_from(model)), 0, model.__name__)

    def test_strict_complete_request_preserves_outfits_when_footwear_is_missing(self):
        first = self.build(count=1)
        assert_complete_batch(self, first)
        with Session(self.engine) as db:
            before_revision = db.get(User, self.user_id).recommendation_revision
            before_history = list(db.scalars(select(MetricEvent.id)))
            db.execute(delete(Product).where(Product.category == 'обувь'))
            db.commit()
        self.assertEqual(self.build(allow_clothing_base=False), [])
        with Session(self.engine) as db:
            self.assertEqual(set(db.scalars(select(Outfit.id))), {row['id'] for row in first})
            self.assertEqual(list(db.scalars(select(MetricEvent.id))), before_history)
            self.assertEqual(db.get(User, self.user_id).recommendation_revision, before_revision)

    def test_save_and_unsave_each_advance_revision_once_only_for_changed_flag(self):
        first = self.build(count=1)[0]
        with Session(self.engine, autoflush=False) as db:
            before_revision = db.get(User, self.user_id).recommendation_revision
            with patch.object(outfit_api, 'user_from_token', side_effect=lambda credentials, session: session.get(User, self.user_id)):
                for action, expected_saved, expected_revision in (
                    (outfit_api.outfits_save, True, before_revision + 1),
                    (outfit_api.outfits_save, True, before_revision + 1),
                    (outfit_api.outfits_unsave, False, before_revision + 2),
                    (outfit_api.outfits_unsave, False, before_revision + 2),
                ):
                    self.assertEqual(action(outfit_id=first['id'], db=db, credentials=None)['status'], 'ok')
                    db.expire_all()
                    self.assertEqual(bool(db.get(Outfit, first['id']).is_saved), expected_saved)
                    self.assertEqual(db.get(User, self.user_id).recommendation_revision, expected_revision)
