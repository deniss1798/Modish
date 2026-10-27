"""Activation completion publishes one honest, replayable Wow snapshot."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from app.db import Base
from app.models import (ActivationSession, FitProfile, MetricEvent, Outfit, Product,
    RecommendationCandidatePool, RecommendationEventV2, StyleProfile, TasteProfile,
    User, UserRecommendationCache)
from app.schemas.product_event import EventRequest
from app.services import activation_service as activation, wow_session as wow
from app.services.product_event_service import record_product_event
from app.services.recommendation_budget import RecommendationRankingTimeout
from app.services.recommendation_revision import RecommendationSnapshotChanged, profile_input_mutation
import test_outfit_session as outfit_fixtures


def seed_wow_catalogue(db):
    """Fifteen real likes, exact M/EU38 and independently labelled bad offers."""
    user_id = outfit_fixtures.seed_outfit_refresh_catalogue(db)
    session = db.get(ActivationSession, user_id)
    session.completed_at = None
    for role, category, title, price in (
        ('top', 'рубашки', 'Женская рубашка хлопковая', Decimal('1200.25')),
        ('bottom', 'брюки', 'Женские брюки прямые', Decimal('1800.50')),
    ):
        for index in range(3, 6):
            sku = f'good-{role}-{index}'
            db.add(Product(id=sku, external_id=sku, source='baon', source_id='outfit-merchant',
                title=title, brand='Synthetic outfit brand', category=category, price=price + index * 100,
                currency='RUB', gender_target='womenswear', colors=['black'], style_tags=['casual', 'minimalism'],
                available_sizes=['M'], feed_raw_json={'size_system': 'LETTER'},
                image_url=f'https://baon.ru/images/{sku}.jpg', product_url=f'https://baon.ru/products/{sku}',
                is_active=1, is_available=1, is_deleted_from_feed=0, availability_status='in_stock',
                last_seen_in_feed_at=datetime.now(timezone.utc)))
    db.flush()
    session.swipe_product_ids = list(db.scalars(select(Product.id).where(Product.id.like('good-%')).order_by(Product.id)))
    db.commit()
    for sku in session.swipe_product_ids:
        activation.swipe_feedback(db, db.get(User, user_id), db.get(ActivationSession, user_id), sku, 'like')
        db.commit()
    return user_id


def row_values(row):
    return deepcopy({column.key: getattr(row, column.key) for column in row.__table__.columns})


def assert_honest_wow(case, response, *, nonempty=True, forbidden=(), clothing_base=False):
    case.assertTrue(response['completed'])
    case.assertTrue(response['completed_at'])
    case.assertLessEqual(len(response['outfits']), 3)
    if nonempty:
        slots = ('top', 'bottom') if clothing_base else ('top', 'bottom', 'shoes')
        outfit_fixtures.assert_complete_batch(case, response['outfits'], forbidden=forbidden, slots=slots)
        if clothing_base:
            for outfit in response['outfits']:
                case.assertEqual(outfit['completeness'], 'clothing_base')
                case.assertEqual(outfit['missing_slots'], ['shoes'])
    else:
        case.assertEqual(response['outfits'], [])
    for outfit in response['outfits']:
        case.assertTrue(outfit['title'])
        case.assertTrue(outfit['explanation'])


class WowSessionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        with Session(self.engine, autoflush=False) as db:
            self.user_id = seed_wow_catalogue(db)

    def build(self):
        return wow.build_wow_response(self.engine, self.user_id)

    def state(self):
        with Session(self.engine) as db:
            state = {model.__name__: [row_values(row) for row in db.scalars(
                select(model).order_by(*model.__table__.primary_key.columns))]
                for model in (ActivationSession, StyleProfile, TasteProfile, Outfit, MetricEvent,
                    RecommendationCandidatePool, UserRecommendationCache, RecommendationEventV2)}
            state['revision'] = db.get(User, self.user_id).recommendation_revision
            return state

    def test_full_catalogue_matches_direct_complete_wow_and_ranking_executes_only_selects(self):
        with Session(self.engine, autoflush=False) as db:
            user = db.get(User, self.user_id)
            expected = activation.complete_wow(db, user, db.get(ActivationSession, self.user_id))
            assert_honest_wow(self, expected)
            expected_items = [(row['title'], tuple(sorted(row['items'].items())), Decimal(str(row['total_price'])))
                for row in expected['outfits']]
            db.rollback()
        original, statements = wow.engine.prepare_outfit_batch, []

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

        before = self.state()
        with patch.object(wow.engine, 'prepare_outfit_batch', side_effect=rank) as ranking:
            response = self.build()
        self.assertEqual(ranking.call_count, 3)
        self.assertEqual(set(statements), {'SELECT'})
        assert_honest_wow(self, response)
        self.assertEqual(len(response['outfits']), 3)
        self.assertEqual([(row['title'], tuple(sorted(row['items'].items())), Decimal(str(row['total_price'])))
            for row in response['outfits']], expected_items)
        after = self.state()
        self.assertEqual(after['TasteProfile'], before['TasteProfile'])
        self.assertEqual(after['RecommendationEventV2'], before['RecommendationEventV2'])
        self.assertEqual(after['revision'], before['revision'] + 1)
        self.assertEqual(len(after['Outfit']), 3)
        self.assertEqual(len([row for row in after['MetricEvent'] if row['name'].startswith('outfit_rotation:')]), 3)
        self.assertEqual(len(after['MetricEvent']), len(before['MetricEvent']) + 3)

    def test_completed_retry_uses_issued_snapshot_without_ranking_or_any_writes(self):
        first = self.build()
        assert_honest_wow(self, first)
        before = self.state()
        with patch.object(wow.engine, 'prepare_outfit_batch', side_effect=AssertionError('A completed retry cannot rank')), \
                patch.object(activation, 'complete_wow', side_effect=AssertionError('A retry cannot enter legacy generation')):
            retry = self.build()
        self.assertEqual([row['id'] for row in retry['outfits']], [row['id'] for row in first['outfits']])
        self.assertEqual(self.state(), before)

    def test_missing_footwear_completes_with_labelled_clothing_base(self):
        with Session(self.engine) as db:
            # Keep the durable merchant rows and likes; only availability changes.
            for row in db.scalars(select(Product).where(Product.category == 'обувь')):
                row.is_available, row.availability_status = 0, 'out_of_stock'
            db.commit()
        before = self.state()
        result = self.build()
        assert_honest_wow(self, result, clothing_base=True)
        after = self.state()
        self.assertIsNotNone(after['ActivationSession'][0]['completed_at'])
        self.assertEqual(after['StyleProfile'][0]['onboarding_step'], 4)
        self.assertEqual(len(after['Outfit']), len(result['outfits']))
        self.assertEqual(len(after['MetricEvent']), len(before['MetricEvent']) + len(result['outfits']))
        self.assertEqual(after['TasteProfile'], before['TasteProfile'])

    def _late_change(self, kind):
        original, changed = wow._publish, []

        def publish(sessions, user_id, proposal):
            if not changed:
                changed.append(next(item.product_id for item in proposal.batches[0].choice.items if item.slot == 'shoes'))
                with sessions() as db:
                    if kind == 'feedback':
                        record_product_event(db, db.get(User, user_id), EventRequest(product_id=changed[0],
                            event_type='dislike', meta={'surface': 'feed'}))
                    elif kind == 'stock':
                        product = db.get(Product, changed[0])
                        product.is_available, product.availability_status = 0, 'out_of_stock'
                        db.commit()
                    else:
                        with profile_input_mutation(db, user_id):
                            db.scalar(select(FitProfile).where(FitProfile.user_id == user_id)).budget_max = 5000
                        db.commit()
            return original(sessions, user_id, proposal)

        with patch.object(wow, '_publish', side_effect=publish) as publishing:
            result = self.build()
        self.assertEqual(publishing.call_count, 2)
        assert_honest_wow(self, result, forbidden=changed, clothing_base=kind == 'fit')
        if kind == 'fit':
            self.assertTrue(all(Decimal(str(row['total_price'])) <= Decimal('5000') for row in result['outfits']))
        with Session(self.engine) as db:
            session = db.get(ActivationSession, self.user_id)
            self.assertEqual([row['id'] for row in session.wow_json], [row['id'] for row in result['outfits']])
            self.assertEqual(db.scalar(select(func.count()).select_from(Outfit)), len(result['outfits']))

    def test_feedback_after_prepare_reranks_and_never_publishes_disliked_sku(self):
        self._late_change('feedback')

    def test_stock_after_prepare_reranks_and_never_publishes_unavailable_sku(self):
        self._late_change('stock')

    def test_fit_budget_after_prepare_reranks_to_labelled_base_within_new_budget(self):
        self._late_change('fit')

    def test_two_late_conflicts_roll_back_all_completion_outfits_rotation_profile_and_cache(self):
        with Session(self.engine) as db:
            db.add(RecommendationCandidatePool(id='previous-pool', user_id=self.user_id, product_ids=['good-top-0']))
            db.add(UserRecommendationCache(id='previous-cache', user_id=self.user_id, top_product_ids=['good-bottom-0']))
            db.commit()
        before, original, observed = self.state(), activation.wow_response, []

        def reject_after_flush(db, user, session, profile, fit, **kwargs):
            result = original(db, user, session, profile, fit, **kwargs)
            assert_honest_wow(self, result)
            self.assertTrue(session.completed_at)
            self.assertTrue(profile.completed_at)
            self.assertEqual(profile.onboarding_step, 4)
            self.assertEqual(db.scalar(select(func.count()).select_from(Outfit)), 3)
            self.assertEqual(db.scalar(select(func.count()).select_from(MetricEvent).where(
                MetricEvent.name.like('outfit_rotation:%'))), 3)
            observed.append(tuple(row['id'] for row in result['outfits']))
            raise RecommendationSnapshotChanged()

        with patch.object(activation, 'wow_response', side_effect=reject_after_flush), \
                self.assertRaises(HTTPException) as caught:
            self.build()
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(len(observed), 2)
        self.assertEqual(self.state(), before)

    def test_fourteen_ratings_cannot_complete_or_publish_any_outfit(self):
        with Session(self.engine) as db:
            session = db.get(ActivationSession, self.user_id)
            session.swipes_json = dict(list(session.swipes_json.items())[:14])
            db.commit()
        before = self.state()
        with patch.object(wow.engine, 'prepare_outfit_batch', side_effect=AssertionError('Admission must fail before ranking')), \
                self.assertRaises(HTTPException) as caught:
            self.build()
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(self.state(), before)

    def test_deadline_after_ranking_publishes_no_completion_outfit_history_or_profile_changes(self):
        before = self.state()
        with patch.object(wow.engine, 'prepare_outfit_batch', wraps=wow.engine.prepare_outfit_batch) as ranking, \
                patch.object(wow, 'check_ranking_budget', side_effect=[None, None, None, RecommendationRankingTimeout()]), \
                self.assertRaises(HTTPException) as caught:
            self.build()
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(ranking.call_count, 3)
        self.assertIn('Повторите', caught.exception.detail)
        self.assertEqual(self.state(), before)
