"""Optional legacy milestone ranking cannot undo or duplicate durable feedback."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, delete, event, func, select
from sqlalchemy.orm import Session

from app.api import recommendations as api
from app.db import Base
from app.models import (ActivationSession, MetricEvent, Outfit, RecommendationCandidatePool,
    RecommendationEventV2, StyleProfile, TasteProfile, User, UserProductState,
    UserRecommendationCache, UserTasteFeature)
from app.schemas.product_event import EventRequest
from app.services import outfit_session as outfits, product_event_service as feedback
import test_outfit_session as fixtures


class MilestoneOutfitSessionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        with Session(self.engine, autoflush=False) as db:
            self.user_id = fixtures.seed_outfit_refresh_catalogue(db)
            db.execute(delete(ActivationSession).where(ActivationSession.user_id == self.user_id))
            self.target = 'good-top-0'
            clock = datetime.now(timezone.utc)
            db.add_all(RecommendationEventV2(id=f'milestone-seed-{index}', user_id=self.user_id,
                product_id=self.target, event_type='view', event_weight=.1,
                meta_json={'synthetic_seed': True}, created_at=clock - timedelta(minutes=index + 1))
                for index in range(19))
            db.commit()

    def send(self, db):
        with patch.object(api, 'user_from_token', return_value=db.get(User, self.user_id)):
            return api.recommendations_events(EventRequest(product_id=self.target, event_type='like',
                meta={'surface': 'feed', 'synthetic_milestone_probe': True}), db=db, credentials=None)

    def event_count(self, db):
        return db.scalar(select(func.count()).select_from(RecommendationEventV2).where(
            RecommendationEventV2.user_id == self.user_id))

    def snapshot(self, db):
        result = {'revision': db.get(User, self.user_id).recommendation_revision}
        for model in (StyleProfile, TasteProfile, UserTasteFeature, Outfit,
                RecommendationCandidatePool, UserRecommendationCache, MetricEvent):
            values = []
            for row in db.scalars(select(model).where(model.user_id == self.user_id)):
                values.append({column.name: deepcopy(getattr(row, column.name)) for column in model.__table__.columns})
            result[model.__tablename__] = sorted(values, key=lambda item: json.dumps(item, sort_keys=True, default=str))
        return result

    def assert_durable_reaction(self, db):
        self.assertEqual(self.event_count(db), 20)
        issued = [row for row in db.scalars(select(RecommendationEventV2).where(
            RecommendationEventV2.user_id == self.user_id)) if row.meta_json.get('synthetic_milestone_probe')]
        self.assertEqual(len(issued), 1)
        self.assertEqual(issued[0].event_type, 'like')
        state = db.scalar(select(UserProductState).where(UserProductState.user_id == self.user_id,
            UserProductState.product_id == self.target))
        self.assertEqual(state.event_strength, 2)
        self.assertIsNotNone(state.hidden_until)
        taste = db.scalar(select(TasteProfile).where(TasteProfile.user_id == self.user_id))
        self.assertEqual(taste.category_weights['рубашки'], 2)

    def test_twentieth_event_is_durable_and_auth_session_released_before_readonly_rank(self):
        original, observed, statements = outfits.engine.prepare_outfit_batch, [], []
        with Session(self.engine, autoflush=False) as auth_db:
            def rank(db, *args, **kwargs):
                self.assertTrue(db.info['recommendation_read_only'])
                self.assertNotIn('_recommendation_owner_mutations', db.info)
                self.assertFalse(auth_db.in_transaction())
                self.assertEqual(len(auth_db.identity_map), 0)
                with Session(self.engine) as verification:
                    self.assert_durable_reaction(verification)
                def record(_connection, _cursor, sql, *_args):
                    statements.append((sql.strip().split()[0].upper(), 'FOR UPDATE' in sql.upper()))
                event.listen(self.engine, 'before_cursor_execute', record)
                try:
                    batch = original(db, *args, **kwargs)
                finally:
                    event.remove(self.engine, 'before_cursor_execute', record)
                observed.append(batch)
                return batch
            with patch.object(outfits.engine, 'prepare_outfit_batch', side_effect=rank):
                response = self.send(auth_db)
        self.assertEqual(len(observed), 1)
        self.assertEqual(set(statements), {('SELECT', False)})
        self.assertEqual((response['status'], response['total_events'], response['milestone_reached']), ('ok', 20, True))
        self.assertGreater(response['outfits_generated'], 0)
        with Session(self.engine) as db:
            self.assert_durable_reaction(db)
            self.assertEqual(db.scalar(select(func.count()).select_from(Outfit)), response['outfits_generated'])
            self.assertEqual(db.scalar(select(func.count()).select_from(MetricEvent).where(
                MetricEvent.name == 'outfit_rotation:daily')), 1)

    def test_optional_rank_failure_returns_success_with_exactly_one_durable_event(self):
        reached = []
        def fail(db, *_args, **_kwargs):
            reached.append(db.info.get('recommendation_read_only'))
            with Session(self.engine) as verification:
                self.assert_durable_reaction(verification)
            raise RuntimeError('SYNTHETIC_OPTIONAL_MILESTONE_FAILURE')
        with Session(self.engine, autoflush=False) as db, \
                patch.object(outfits.engine, 'prepare_outfit_batch', side_effect=fail) as ranking, \
                patch.object(feedback.logger, 'exception') as logging:
            response = self.send(db)
        ranking.assert_called_once()
        logging.assert_called_once()
        self.assertEqual(reached, [True])
        self.assertEqual((response['status'], response['total_events'], response['outfits_generated']), ('ok', 20, 0))
        with Session(self.engine) as db:
            self.assert_durable_reaction(db)
            for model in (Outfit, RecommendationCandidatePool, UserRecommendationCache):
                self.assertEqual(db.scalar(select(func.count()).select_from(model)), 0)

    def test_existing_outfit_skips_rank_without_replacement_history_or_extra_revision(self):
        with Session(self.engine) as db:
            db.add(Outfit(id='already-issued-milestone', user_id=self.user_id, style_direction='daily',
                items_json={'top': 'good-top-0', 'bottom': 'good-bottom-0', 'shoes': 'good-shoes-0'},
                total_price=5001, is_saved=1))
            db.commit()
            revision = db.get(User, self.user_id).recommendation_revision
        with Session(self.engine, autoflush=False) as db, \
                patch.object(outfits.engine, 'prepare_outfit_batch', side_effect=AssertionError('Existing outfit must skip rank')) as ranking:
            response = self.send(db)
        ranking.assert_not_called()
        self.assertEqual(response['outfits_generated'], 0)
        with Session(self.engine) as db:
            self.assert_durable_reaction(db)
            self.assertEqual(set(db.scalars(select(Outfit.id))), {'already-issued-milestone'})
            self.assertEqual(db.get(User, self.user_id).recommendation_revision, revision + 1)
            for model in (RecommendationCandidatePool, UserRecommendationCache):
                self.assertEqual(db.scalar(select(func.count()).select_from(model)), 0)
            self.assertEqual(db.scalar(select(func.count()).select_from(MetricEvent).where(
                MetricEvent.name == 'outfit_rotation:daily')), 0)

    def test_activation_owner_never_starts_legacy_milestone_rank(self):
        with Session(self.engine) as db:
            db.add(ActivationSession(user_id=self.user_id))
            db.commit()
        with Session(self.engine, autoflush=False) as db, \
                patch.object(outfits, 'build_outfits_response', side_effect=AssertionError('Activation owner must skip legacy milestone')) as building:
            response = self.send(db)
        building.assert_not_called()
        self.assertFalse(response['milestone_reached'])
        self.assertEqual(response['outfits_generated'], 0)
        with Session(self.engine) as db:
            self.assert_durable_reaction(db)
            self.assertEqual(db.scalar(select(func.count()).select_from(Outfit)), 0)

    def test_concurrent_normal_publication_between_phases_wins_without_double_rotation(self):
        original, winner, after_winner = outfits._publish, [], []
        def publish(sessions, user_id, proposal, **options):
            if not winner:
                concurrent = outfits._prepare(sessions, user_id, count=1, scenario='daily')
                rows = original(sessions, user_id, concurrent, replace_existing=True)
                self.assertTrue(rows)
                winner.extend(row['id'] for row in rows)
                with Session(self.engine) as db:
                    after_winner.append(self.snapshot(db))
            return original(sessions, user_id, proposal, **options)
        with Session(self.engine, autoflush=False) as db, patch.object(outfits, '_publish', side_effect=publish) as publishing:
            response = self.send(db)
        self.assertEqual(publishing.call_count, 2)
        self.assertEqual(response['outfits_generated'], 0)
        with Session(self.engine) as db:
            self.assert_durable_reaction(db)
            self.assertEqual(self.snapshot(db), after_winner[0])
            self.assertEqual(set(db.scalars(select(Outfit.id))), set(winner))
            self.assertEqual(db.scalar(select(func.count()).select_from(MetricEvent).where(
                MetricEvent.name == 'outfit_rotation:daily')), 1)

    def test_existing_outfit_noop_does_not_initialize_missing_profile_graph(self):
        with Session(self.engine) as db:
            db.add(Outfit(id='legacy-existing-without-profiles', user_id=self.user_id,
                style_direction='daily', items_json={}, total_price=0))
            db.execute(delete(StyleProfile).where(StyleProfile.user_id == self.user_id))
            db.execute(delete(TasteProfile).where(TasteProfile.user_id == self.user_id))
            db.commit()
            baseline = self.snapshot(db)
        with patch.object(outfits.engine, 'prepare_outfit_batch', side_effect=AssertionError('Noop cannot rank')):
            self.assertEqual(outfits.build_outfits_response(self.engine, self.user_id, only_if_empty=True), [])
        with Session(self.engine) as db:
            self.assertEqual(self.snapshot(db), baseline)

    def test_postcommit_count_failure_still_reports_one_accepted_reaction(self):
        committed, reached = [], []
        with Session(self.engine, autoflush=False) as db:
            event.listen(db, 'after_commit', lambda _db: committed.append(True))
            original = db.scalar
            def fail_count(statement, *args, **kwargs):
                sql = str(statement)
                if committed and 'count(' in sql.lower() and 'recommendation_events_v2' in sql:
                    reached.append(True)
                    raise RuntimeError('SYNTHETIC_POSTCOMMIT_COUNT_FAILURE')
                return original(statement, *args, **kwargs)
            with patch.object(db, 'scalar', side_effect=fail_count), \
                    patch.object(feedback.logger, 'exception') as logging, \
                    patch.object(outfits, 'build_outfits_response') as generation:
                response = self.send(db)
        self.assertEqual(reached, [True])
        logging.assert_called_once()
        generation.assert_not_called()
        self.assertEqual((response['status'], response['milestone_reached'], response['outfits_generated']), ('ok', False, 0))
        self.assertIsNone(response['total_events'])
        with Session(self.engine) as db:
            self.assert_durable_reaction(db)
            self.assertEqual(db.scalar(select(func.count()).select_from(Outfit)), 0)

    def test_postcommit_activation_lookup_failure_cannot_turn_accepted_reaction_into_error(self):
        committed, reached = [], []
        with Session(self.engine, autoflush=False) as db:
            event.listen(db, 'after_commit', lambda _db: committed.append(True))
            original = db.get
            def fail_activation(model, *args, **kwargs):
                if committed and model is ActivationSession:
                    reached.append(True)
                    raise RuntimeError('SYNTHETIC_POSTCOMMIT_ACTIVATION_FAILURE')
                return original(model, *args, **kwargs)
            with patch.object(db, 'get', side_effect=fail_activation), \
                    patch.object(feedback.logger, 'exception') as logging, \
                    patch.object(outfits, 'build_outfits_response') as generation:
                response = self.send(db)
        self.assertEqual(reached, [True])
        logging.assert_called_once()
        generation.assert_not_called()
        self.assertEqual((response['status'], response['milestone_reached'], response['outfits_generated']), ('ok', False, 0))
        self.assertEqual(response['total_events'], 20)
        with Session(self.engine) as db:
            self.assert_durable_reaction(db)
            self.assertEqual(db.scalar(select(func.count()).select_from(Outfit)), 0)


if __name__ == '__main__':
    unittest.main()
