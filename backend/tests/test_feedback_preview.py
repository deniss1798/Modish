"""Prospective feedback must equal accepted feedback without speculative writes."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest
from unittest.mock import patch

from sqlalchemy import event, select

from app.models import (Product, RecommendationEventV2, StyleProfile, TasteProfile,
    User, UserTasteFeature)
from app.schemas.product_event import EventRequest
from app.services import feedback_preview as previews, product_event_service as events
from app.services.behavior_profile import aggregate_behavior, load_behavior_profile
from app.services.legacy_taste_reducer import LIST_FIELDS, WEIGHT_FIELDS
from app.services.mie_scoring import load_user_taste_feature_map
from app.services.personalization_scoring import AffinityScorer, load_ranking_seen_counts
from app.services.profile_repository import load_preferences
from app.services.recommendation_config import VALID_DISLIKE_REASONS
from app.services.recommendation_revision import RecommendationSnapshotChanged
from app.services.user_twin_service import apply_event_to_user_twin, preview_user_twin_features
import test_intent_session as fixtures


def normalized(value):
    if isinstance(value, datetime):
        return previews._utc(value).isoformat()
    if isinstance(value, dict):
        return {key: normalized(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [normalized(item) for item in value]
    return value


class FeedbackPreviewTests(unittest.TestCase):
    def setUp(self):
        fixtures.IntentSessionTests.setUp(self)
        self.clock = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
        self.product_id = 'z-good-000'
        with self.sessions() as db:
            product = db.get(Product, self.product_id)
            product.title = 'Женские прямые хлопковые брюки'
            product.price = Decimal('6000.99')
            product.colors, product.style_tags = ['black'], ['minimalism']
            taste = db.scalar(select(TasteProfile).where(TasteProfile.user_id == self.user_id))
            taste.category_weights = {'брюки': 2.25, 'unrelated': 1}
            taste.brand_weights = {'synthetic brand': 1.5}
            taste.color_weights, taste.style_weights = {'black': 2}, {'minimalism': -1}
            apply_event_to_user_twin(db, user_id=self.user_id, product=product,
                event_type='like', occurred_at=self.clock - timedelta(days=30))
            db.commit()

    product = fixtures.IntentSessionTests.product

    def baseline(self, db):
        taste = db.scalar(select(TasteProfile).where(TasteProfile.user_id == self.user_id))
        features = list(db.scalars(select(UserTasteFeature).where(UserTasteFeature.user_id == self.user_id)))
        product = db.get(Product, self.product_id)
        return taste, features, product

    def preview(self, db, **options):
        taste, features, product = self.baseline(db)
        return previews.preview_dislike(taste, features, product, user_id=self.user_id,
            occurred_at=options.pop('occurred_at', self.clock), **options)

    def feature_values(self, rows):
        names = [column.name for column in UserTasteFeature.__table__.columns if column.name != 'id']
        return {(row.feature_type, row.feature_value): normalized({name: getattr(row, name) for name in names})
            for row in rows}

    def ranking_inputs(self, db, clock, *, user_id=None):
        user_id = user_id or self.user_id
        profile, fit, taste = load_preferences(db, db.get(User, user_id))
        features = load_user_taste_feature_map(db, user_id=user_id, as_of=clock)
        behavior = load_behavior_profile(db, user_id=user_id, as_of=clock)
        seen = load_ranking_seen_counts(db, user_id=user_id, as_of=clock)
        scorer = AffinityScorer(profile, taste, fit=fit, taste_features=features, seen_counts=seen)
        scores = {pid: scorer.evaluate(db.get(Product, pid)) for pid in (self.product_id, 'z-good-001')}
        return {'taste': normalized({name: getattr(taste, name) for name in (*LIST_FIELDS, *WEIGHT_FIELDS,
            'profile_version', 'updated_at')}), 'features': features, 'behavior': behavior, 'seen': seen,
            'scores': scores, 'taste_behavior': deepcopy(getattr(taste, 'behavior_snapshot', {}))}

    def accept(self, db, *, meta, clock):
        class Clock:
            @staticmethod
            def now(_timezone=None):
                return clock
        with patch.object(events, 'datetime', Clock):
            events.record_product_event(db, db.get(User, self.user_id),
                EventRequest(product_id=self.product_id, event_type='dislike', meta=meta), commit=False)

    def test_each_reason_preview_equals_real_reducer_features_behavior_seen_and_scores(self):
        for reason in (None, *sorted(VALID_DISLIKE_REASONS)):
            with self.subTest(reason=reason), self.sessions() as db:
                meta = {'surface': 'feedback_preview_test', 'reason': reason}
                before_taste, before_features, _ = self.baseline(db)
                original = normalized({column.name: getattr(before_taste, column.name)
                    for column in TasteProfile.__table__.columns})
                original_features = self.feature_values(before_features)
                preview = self.preview(db, meta=meta)
                self.assertEqual(self.feature_values(before_features), original_features)
                self.assertEqual(normalized({column.name: getattr(before_taste, column.name)
                    for column in TasteProfile.__table__.columns}), original)
                self.assertFalse(db.new or db.dirty or db.deleted)
                db.info.update(recommendation_read_only=True, recommendation_as_of=self.clock)
                statements = []
                def record(_connection, _cursor, sql, *_args):
                    statements.append((sql.strip().split()[0].upper(), 'FOR UPDATE' in sql.upper()))
                event.listen(self.engine, 'before_cursor_execute', record)
                try:
                    with previews.use_feedback_preview(db, preview):
                        expected = self.ranking_inputs(db, self.clock)
                finally:
                    event.remove(self.engine, 'before_cursor_execute', record)
                self.assertTrue(statements)
                self.assertEqual(set(statements), {('SELECT', False)})
                self.assertFalse(db.new or db.dirty or db.deleted)
                db.info.pop('recommendation_read_only')
                self.accept(db, meta=meta, clock=self.clock)
                actual = self.ranking_inputs(db, self.clock)
                self.assertEqual(actual, expected)
                self.assertEqual(self.feature_values(db.scalars(select(UserTasteFeature).where(
                    UserTasteFeature.user_id == self.user_id))), self.feature_values(preview.features))
                accepted = db.scalar(select(RecommendationEventV2).where(RecommendationEventV2.user_id == self.user_id))
                self.assertEqual(accepted.meta_json, preview.events[0].metadata())
                self.assertEqual(accepted.meta_json['observed_price'], 6000.99)
                self.assertEqual(normalized(accepted.created_at), normalized(preview.as_of))
                db.rollback()

    def test_repeated_signals_use_same_decay_confidence_and_distinct_day_rules(self):
        for count in (2, 7):
            with self.subTest(count=count), self.sessions() as db:
                taste, features, product = self.baseline(db)
                preview = None
                for index in range(count):
                    preview = previews.preview_dislike(taste, features, product, user_id=self.user_id,
                        meta={'reason': 'dont_like_color'}, occurred_at=self.clock + timedelta(minutes=index), previous=preview)
                db.info.update(recommendation_read_only=True, recommendation_as_of=preview.as_of)
                with previews.use_feedback_preview(db, preview):
                    expected = self.ranking_inputs(db, preview.as_of)
                self.assertEqual(expected['seen'][self.product_id], 1)
                db.info.pop('recommendation_read_only')
                for index in range(count):
                    self.accept(db, meta={'reason': 'dont_like_color'}, clock=self.clock + timedelta(minutes=index))
                actual = self.ranking_inputs(db, preview.as_of)
                self.assertEqual(actual, expected)
                self.assertEqual(self.feature_values(db.scalars(select(UserTasteFeature).where(
                    UserTasteFeature.user_id == self.user_id))), self.feature_values(preview.features))
                color = next(row for row in preview.features if row.feature_type == 'color' and row.feature_value == 'black')
                self.assertEqual(color.negative_count, count)
                self.assertLessEqual(color.preference_score, -.4)
                db.rollback()

    def test_seen_overlay_counts_new_days_but_not_existing_or_repeated_same_day(self):
        with self.sessions() as db:
            for key, clock in (('today', self.clock - timedelta(hours=1)),
                    ('yesterday', self.clock - timedelta(days=1)), ('too-old', self.clock - timedelta(days=91))):
                db.add(RecommendationEventV2(id=key, user_id=self.user_id, product_id=self.product_id,
                    event_type='view', event_weight=.1, meta_json={}, created_at=clock))
            db.commit()
            taste, features, product = self.baseline(db)
            first = previews.preview_dislike(taste, features, product, user_id=self.user_id, occurred_at=self.clock)
            again = previews.preview_dislike(taste, features, product, user_id=self.user_id,
                occurred_at=self.clock + timedelta(minutes=1), previous=first)
            tomorrow = previews.preview_dislike(taste, features, product, user_id=self.user_id,
                occurred_at=self.clock + timedelta(days=1), previous=again)
            for preview, expected in ((first, 2), (again, 2), (tomorrow, 3)):
                db.info.update(recommendation_read_only=True, recommendation_as_of=preview.as_of)
                with previews.use_feedback_preview(db, preview):
                    self.assertEqual(load_ranking_seen_counts(db, user_id=self.user_id)[self.product_id], expected)
            db.info.pop('recommendation_read_only')

    def test_observed_metadata_is_frozen_and_overrides_changed_catalogue(self):
        with self.sessions() as db:
            preview = self.preview(db, meta={'reason': 'too_expensive', 'observed_price': 1,
                'observed_brand': 'client-supplied', 'nested': {'values': ['original']}})
            metadata = preview.events[0].metadata()
            metadata['nested']['values'].append('changed-copy')
            self.assertEqual(preview.events[0].metadata()['nested']['values'], ['original'])
            self.accept(db, meta={'reason': 'too_expensive', 'observed_price': 1,
                'observed_brand': 'client-supplied', 'nested': {'values': ['original']}}, clock=self.clock)
            expected = load_behavior_profile(db, user_id=self.user_id, as_of=self.clock)
            db.rollback()
            product = db.get(Product, self.product_id)
            product.brand, product.category, product.price = 'Changed catalogue brand', 'обувь', Decimal('2.99')
            db.commit()
            db.info.update(recommendation_read_only=True, recommendation_as_of=self.clock)
            with previews.use_feedback_preview(db, preview):
                actual = load_behavior_profile(db, user_id=self.user_id)
            self.assertEqual(actual, expected)
            self.assertIn('synthetic brand', actual['brands'])
            self.assertEqual(actual['category_prices']['брюки']['rejected_price_median'], 6000.99)
            self.assertNotIn('обувь', actual['category_prices'])

    def test_overlay_copies_cache_scope_and_other_owner_are_isolated(self):
        with self.sessions() as db:
            other = User(id='other-preview-owner', email='other-preview@example.test', password_hash='unused')
            db.add_all([other, StyleProfile(id='other-preview-style', user_id=other.id),
                TasteProfile(id='other-preview-taste', user_id=other.id)])
            db.commit()
            first = self.preview(db, meta={'reason': 'dont_like_color'})
            second = self.preview(db, meta={'reason': 'dont_like_brand'})
            db.info.update(recommendation_read_only=True, recommendation_as_of=self.clock)
            with previews.use_feedback_preview(db, first):
                expected = self.ranking_inputs(db, self.clock)
                copied = load_preferences(db, db.get(User, self.user_id))[2]
                copied.color_weights['only-in-copy'] = 55
                copied.behavior_snapshot['brands'].clear()
                mutable = load_user_taste_feature_map(db, user_id=self.user_id)
                mutable.clear()
                behavior = load_behavior_profile(db, user_id=self.user_id)
                behavior['brands'].clear()
                seen = load_ranking_seen_counts(db, user_id=self.user_id)
                seen.clear()
                self.assertEqual(self.ranking_inputs(db, self.clock), expected)
                self.assertEqual(load_user_taste_feature_map(db, user_id=other.id), {})
                self.assertEqual(load_ranking_seen_counts(db, user_id=other.id), {})
                self.assertEqual(load_preferences(db, other)[2].color_weights, {})
                with self.assertRaisesRegex(RuntimeError, 'SYNTHETIC_SCOPE_FAILURE'):
                    with previews.use_feedback_preview(db, second):
                        self.assertNotEqual(self.ranking_inputs(db, self.clock)['features'], expected['features'])
                        raise RuntimeError('SYNTHETIC_SCOPE_FAILURE')
                self.assertEqual(self.ranking_inputs(db, self.clock), expected)
            self.assertIsNone(previews.current_feedback_preview(db, self.user_id))
            self.assertEqual(load_ranking_seen_counts(db, user_id=self.user_id), {})
            with self.sessions() as independent:
                self.assertIsNone(previews.current_feedback_preview(independent, self.user_id))
            with self.assertRaises(FrozenInstanceError):
                first.user_id = other.id

    def test_scope_rejects_wrong_clock_write_session_and_pending_orm_changes(self):
        with self.sessions() as db:
            preview = self.preview(db)
            with self.assertRaises(ValueError):
                with previews.use_feedback_preview(db, preview):
                    self.fail('Writable session cannot install preview')
            db.info.update(recommendation_read_only=True, recommendation_as_of=self.clock + timedelta(seconds=1))
            with self.assertRaises(ValueError):
                with previews.use_feedback_preview(db, preview):
                    self.fail('Mismatched clock cannot install preview')
            db.info['recommendation_as_of'] = self.clock
            with previews.use_feedback_preview(db, preview):
                with self.assertRaises(ValueError):
                    load_user_taste_feature_map(db, user_id=self.user_id, as_of=self.clock + timedelta(seconds=1))
            db.get(Product, self.product_id).title = 'Uncommitted catalogue edit'
            with self.assertRaises(ValueError):
                with previews.use_feedback_preview(db, preview):
                    self.fail('Dirty session cannot install preview')

    def test_overlay_does_not_fabricate_missing_persisted_profile_graph(self):
        for model in (TasteProfile, StyleProfile):
            with self.subTest(model=model.__name__), self.sessions() as db:
                preview = self.preview(db)
                db.delete(db.scalar(select(model).where(model.user_id == self.user_id)))
                db.flush()
                db.info.update(recommendation_read_only=True, recommendation_as_of=self.clock)
                with previews.use_feedback_preview(db, preview):
                    with self.assertRaises(RecommendationSnapshotChanged):
                        load_preferences(db, db.get(User, self.user_id))
                self.assertFalse(db.new or db.dirty or db.deleted)
                db.rollback()

    def test_pure_helpers_reject_owner_mismatch_without_mutating_inputs(self):
        with self.sessions() as db:
            taste, features, product = self.baseline(db)
            before = self.feature_values(features)
            with self.assertRaises(ValueError):
                previews.preview_dislike(taste, features, product, user_id='another-owner', occurred_at=self.clock)
            with self.assertRaises(ValueError):
                preview_user_twin_features(features, user_id='another-owner', product=product,
                    event_type='dislike', occurred_at=self.clock)
            with self.assertRaises(ValueError):
                self.preview(db, meta={'reason': 'unsupported-reason'})
            preview = self.preview(db)
            with self.assertRaises(ValueError):
                replace(preview, user_id='another-owner')
            with self.assertRaises(ValueError):
                replace(preview, as_of=self.clock - timedelta(seconds=1))
            self.assertEqual(self.feature_values(features), before)
            self.assertFalse(db.new or db.dirty or db.deleted)

    def test_behavior_merge_uses_database_decoded_day_near_midnight(self):
        # Model PostgreSQL's +03 session decoding of one UTC timestamptz. The
        # prospective dislike belongs to the same DB day as an earlier view.
        database_timezone = timezone(timedelta(hours=3))
        clock = datetime(2026, 10, 15, 22, 0, tzinfo=timezone.utc)
        with self.sessions() as db:
            preview = self.preview(db, occurred_at=clock, meta={'reason': 'dont_like_brand'})
        prior = (self.product_id, 'Synthetic brand', 'брюки', 'view', {},
            (clock - timedelta(minutes=1)).astimezone(database_timezone))
        merged = aggregate_behavior(previews.merge_behavior_rows([prior], preview, as_of=clock,
            database_clocks=(clock.astimezone(database_timezone),)))
        accepted = aggregate_behavior([preview.events[0].behavior_row(
            database_clock=clock.astimezone(database_timezone)), prior])
        self.assertEqual(merged, accepted)
        self.assertEqual(merged['brands']['synthetic brand']['exposure_count'], 1)
        with self.assertRaises(ValueError):
            list(previews.merge_behavior_rows([], preview, as_of=clock, database_clocks=()))


if __name__ == '__main__':
    unittest.main()
