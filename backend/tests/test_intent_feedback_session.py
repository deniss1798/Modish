"""Permanent feedback and its issued successor must commit or roll back together."""
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from app.api import shopping_intents as api
from app.models import (ActivationExposure, FitProfile, MetricEvent, Product,
    RecommendationEventV2, ShoppingIntent, SourceRule, TasteProfile, User,
    UserProductState, UserTasteFeature)
from app.schemas.shopping_intent import IntentFeedbackRequest
from app.services import intent_mutation_session as mutations, product_event_service as events
from app.services import shopping_intent_service as service
from app.services.feedback_preview import current_feedback_preview
from app.services.mie_scoring import load_user_taste_feature_map
from app.services.profile_repository import load_preferences
from app.services.recommendation_revision import profile_input_mutation, profile_input_signature
import test_intent_mutation_session as fixtures
import test_intent_http as http_fixtures


def normalized(value):
    if isinstance(value, datetime):
        return service._utc(value).isoformat()
    if isinstance(value, dict):
        return {key: normalized(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalized(item) for item in value]
    return value


class IntentFeedbackSessionTests(unittest.TestCase):
    metrics = fixtures.IntentMutationSessionTests.metrics
    single = fixtures.IntentMutationSessionTests.single
    outfit = fixtures.IntentMutationSessionTests.outfit

    def product(self, db, sku, **changes):
        changes.setdefault('colors', ['black'])
        changes.setdefault('style_tags', ['minimalism'])
        return fixtures.IntentMutationSessionTests.product(self, db, sku, **changes)

    def setUp(self):
        fixtures.IntentMutationSessionTests.setUp(self)
        self.clock = datetime.now(timezone.utc).replace(microsecond=123456)
        with self.sessions() as db:
            for product in db.scalars(select(Product)):
                product.colors, product.style_tags = ['black'], ['minimalism']
            db.commit()

    def target(self, first):
        return first['products'][0]['product']['id']

    def prepare(self, first, product_id=None, reason='dont_like_color'):
        with patch.object(mutations, '_now', return_value=self.clock):
            return mutations._prepare(self.sessions, self.user_id, first['id'],
                mutations.MutationRequest('feedback', first['version'], product_id=product_id or self.target(first), reason=reason))

    def feedback(self, first, product_id=None, reason='dont_like_color'):
        with patch.object(mutations, '_now', return_value=self.clock):
            return mutations.build_mutation_response(self.engine, self.user_id, first['id'], operation='feedback',
                expected_version=first['version'], product_id=product_id or self.target(first), reason=reason)

    def seed_exposure(self, product_id):
        with self.sessions() as db:
            db.add(ActivationExposure(id='existing-feedback-exposure', user_id=self.user_id, product_id=product_id,
                position=1, reaction='like', saved=1, opened=1, clicked=1, shown_at=self.clock - timedelta(hours=1)))
            db.commit()

    def snapshot(self, db, intent_id, *, semantic=False):
        intent = db.get(ShoppingIntent, intent_id)
        values = {'intent': {column.name: getattr(intent, column.name) for column in ShoppingIntent.__table__.columns},
            'revision': db.get(User, self.user_id).recommendation_revision}
        for model in (TasteProfile, UserTasteFeature, UserProductState, ActivationExposure,
                RecommendationEventV2, MetricEvent):
            records = []
            for row in db.scalars(select(model).where(model.user_id == self.user_id)):
                record = {column.name: getattr(row, column.name) for column in model.__table__.columns
                    if not semantic or column.name != 'id'}
                if semantic and model is MetricEvent:
                    record.pop('created_at')
                if semantic and model is UserProductState:
                    # Legacy default-clock insertion triggers a second ORM
                    # onupdate. Fixed-clock publication deliberately pins it.
                    record.pop('updated_at')
                records.append(normalized(record))
            values[model.__tablename__] = sorted(records, key=lambda row: json.dumps(row, sort_keys=True))
        return normalized(values)

    def state(self, first):
        with self.sessions() as db:
            return self.snapshot(db, first['id'])

    def count_events(self):
        with self.sessions() as db:
            return db.scalar(select(func.count()).select_from(RecommendationEventV2).where(
                RecommendationEventV2.user_id == self.user_id))

    def test_readonly_preview_sees_dislike_before_event_and_preserves_real_signature(self):
        first = self.single()
        before, original, seen = self.state(first), mutations.item_results, []
        with self.sessions() as db:
            signature = profile_input_signature(db, self.user_id)
        def rank(db, user, spec, **options):
            self.assertTrue(db.info['recommendation_read_only'])
            self.assertNotIn('_recommendation_owner_mutations', db.info)
            preview = current_feedback_preview(db, user.id)
            self.assertIsNotNone(preview)
            self.assertEqual(preview.as_of, self.clock)
            taste = load_preferences(db, user)[2]
            self.assertEqual(taste.color_weights['black'], -3)
            features = load_user_taste_feature_map(db, user_id=user.id)
            self.assertLess(features[('color', 'black')][0], 0)
            self.assertEqual(db.scalar(select(func.count()).select_from(RecommendationEventV2)), 0)
            seen.append(preview)
            return original(db, user, spec, **options)
        statements = []
        def record(_connection, _cursor, sql, *_args):
            statements.append((sql.strip().split()[0].upper(), 'FOR UPDATE' in sql.upper()))
        event.listen(self.engine, 'before_cursor_execute', record)
        try:
            with patch.object(mutations, 'item_results', side_effect=rank):
                proposal = self.prepare(first)
        finally:
            event.remove(self.engine, 'before_cursor_execute', record)
        self.assertEqual(set(statements), {('SELECT', False)})
        self.assertEqual(proposal.profile_signature, signature)
        self.assertEqual(len(seen), 1)
        self.assertEqual(self.state(first), before)
        self.assertNotIn(self.target(first), {row.product_id for row in proposal.products})
        self.assertIn(self.target(first), {row.product_id for row in proposal.evidence})
        with self.assertRaises(FrozenInstanceError):
            proposal.plan.feedback.occurred_at = self.clock + timedelta(seconds=1)
        result = mutations._publish(self.sessions, self.user_id, proposal)
        self.assertEqual(result['version'], 2)
        self.assertEqual(self.count_events(), 1)

    def test_each_public_reason_matches_legacy_effects_and_response_at_fixed_clock(self):
        reasons = (None, 'not_my_style', 'too_expensive', 'dont_like_color', 'dont_like_fit',
            'dont_like_brand', 'already_have_similar', 'not_for_context')
        class Clock:
            @staticmethod
            def now(_timezone=None):
                return self.clock
        for index, reason in enumerate(reasons):
            if index:
                self.setUp()
            first = self.single()
            product_id = self.target(first)
            self.seed_exposure(product_id)
            with self.subTest(reason=reason):
                with self.sessions() as db:
                    db.info['recommendation_as_of'] = self.clock
                    with patch.object(service, 'datetime', Clock), patch.object(events, 'datetime', Clock):
                        intent = service.feedback(db, db.get(User, self.user_id), first['id'], expected_version=1,
                            product_id=product_id, reason=reason)
                        expected_response = service.intent_response(db, db.get(User, self.user_id), intent, remember=True)
                        db.flush()
                    expected = self.snapshot(db, first['id'], semantic=True)
                    db.rollback()
                actual_response = self.feedback(first, product_id, reason)
                with self.sessions() as db:
                    actual = self.snapshot(db, first['id'], semantic=True)
                    self.assertEqual(actual_response, expected_response)
                    self.assertEqual(actual, expected)

    def test_http_releases_auth_session_and_applies_one_revision_with_old_version_metadata(self):
        first = self.single()
        target = self.target(first)
        self.seed_exposure(target)
        with self.sessions() as db:
            before_revision = db.get(User, self.user_id).recommendation_revision
            with patch.object(api, 'user_from_token', return_value=db.get(User, self.user_id)), \
                    patch.object(mutations, '_now', return_value=self.clock):
                result = api.feedback(first['id'], IntentFeedbackRequest(expected_version=1,
                    product_id=target, reason='dont_like_color'), db=db, credentials=None)
            self.assertFalse(db.in_transaction())
            self.assertEqual(len(db.identity_map), 0)
        self.assertEqual(result['version'], 2)
        with self.sessions() as db:
            self.assertEqual(db.get(User, self.user_id).recommendation_revision, before_revision + 1)
            event_row = db.scalar(select(RecommendationEventV2).where(RecommendationEventV2.user_id == self.user_id))
            product = db.get(Product, target)
            self.assertEqual(event_row.meta_json['intent_version'], 1)
            self.assertEqual(event_row.meta_json['intent_id'], first['id'])
            self.assertEqual(event_row.meta_json['observed_price'], float(product.price))
            self.assertEqual(normalized(event_row.created_at), normalized(self.clock))
            state = db.scalar(select(UserProductState).where(UserProductState.product_id == target))
            self.assertEqual(state.event_strength, -3)
            self.assertEqual(normalized(state.hidden_until), normalized(self.clock + timedelta(days=3650)))
            self.assertEqual(normalized(state.updated_at), normalized(self.clock))
            exposure = db.get(ActivationExposure, 'existing-feedback-exposure')
            self.assertEqual((exposure.reaction, exposure.saved, exposure.opened, exposure.clicked), ('dislike', 1, 1, 1))
        self.assertEqual(self.metrics('intent_feedback'), 1)
        self.assertEqual(self.metrics('intent_updated'), 1)
        self.assertEqual(self.metrics('reason_dislike'), 1)
        self.assertEqual(self.metrics('brand_affinity_updated'), 1)
        self.assertEqual(self.metrics(), 2)

    def test_task_only_reasons_do_not_change_personal_state_or_create_exposures(self):
        first = self.single()
        before = self.state(first)
        result = self.feedback(first, reason='not_for_context')
        self.assertNotIn(self.target(first), {row['product']['id'] for row in result['products']})
        after = self.state(first)
        for key in ('revision', 'taste_profiles', 'user_taste_features', 'user_product_states',
                'activation_exposures', 'recommendation_events_v2'):
            self.assertEqual(after[key], before[key])
        cap = int(Decimal(str(result['products'][0]['product']['price'])) * Decimal('.8'))
        result = self.feedback(result, reason='too_expensive')
        self.assertEqual(result['constraints']['budget_max'], cap)
        self.assertEqual(self.count_events(), 0)
        self.assertEqual(self.metrics('reason_dislike'), 2)

    def test_competing_same_version_publisher_loser_does_not_accept_second_feedback(self):
        first = self.single()
        left, right = self.prepare(first), self.prepare(first, reason='dont_like_brand')
        mutations._publish(self.sessions, self.user_id, left)
        before = self.state(first)
        with self.assertRaises(service.IntentError) as error:
            mutations._publish(self.sessions, self.user_id, right)
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(self.state(first), before)
        with patch.object(mutations, 'item_results', side_effect=AssertionError('Old version cannot rank')):
            with self.assertRaises(HTTPException) as replay:
                self.feedback(first)
        self.assertEqual(replay.exception.status_code, 409)
        self.assertEqual(self.count_events(), 1)

    def test_catalogue_conflict_reranks_once_then_observes_new_price(self):
        first = self.single()
        target, original, proposals = self.target(first), mutations._publish, []
        def publish(sessions, user_id, proposal):
            proposals.append(proposal)
            if len(proposals) == 1:
                with self.sessions() as db:
                    db.get(Product, target).price = Decimal('6001.99')
                    db.commit()
            return original(sessions, user_id, proposal)
        with patch.object(mutations, '_publish', side_effect=publish):
            result = self.feedback(first)
        self.assertEqual((len(proposals), result['version'], self.count_events()), (2, 2, 1))
        with self.sessions() as db:
            row = db.scalar(select(RecommendationEventV2))
            self.assertEqual(row.meta_json['observed_price'], 6001.99)
        self.assertEqual(self.metrics('intent_feedback'), 1)

    def test_personal_conflict_reranks_on_current_budget_without_duplicate_signal(self):
        first = self.single()
        original, proposals = mutations._publish, []
        def publish(sessions, user_id, proposal):
            proposals.append(proposal)
            if len(proposals) == 1:
                with self.sessions() as db:
                    with profile_input_mutation(db, self.user_id):
                        db.scalar(select(FitProfile).where(FitProfile.user_id == self.user_id)).budget_max = 500
                        self.product(db, 'new-cheap', price=Decimal('499.99'))
                    db.commit()
            return original(sessions, user_id, proposal)
        with patch.object(mutations, '_publish', side_effect=publish):
            result = self.feedback(first)
        self.assertEqual(len(proposals), 2)
        self.assertEqual([row['product']['id'] for row in result['products']], ['new-cheap'])
        self.assertEqual(self.count_events(), 1)
        self.assertEqual(self.metrics('intent_feedback'), 1)

    def test_two_conflicts_or_timeout_leave_feedback_and_existing_version_untouched(self):
        first = self.single()
        before = self.state(first)
        with patch.object(mutations, '_publish', side_effect=mutations.RecommendationSnapshotChanged) as publish:
            with self.assertRaises(HTTPException) as error:
                self.feedback(first)
        self.assertEqual((error.exception.status_code, publish.call_count), (409, 2))
        self.assertEqual(self.state(first), before)
        with patch.object(mutations, 'check_ranking_budget', side_effect=mutations.RecommendationRankingTimeout):
            with self.assertRaises(HTTPException) as error:
                self.feedback(first)
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(self.state(first), before)

    def test_failure_after_event_application_rolls_back_all_personal_and_intent_writes(self):
        first = self.single()
        self.seed_exposure(self.target(first))
        before = self.state(first)
        with patch.object(service, 'remember_results', side_effect=RuntimeError('SYNTHETIC_PUBLICATION_FAILURE')):
            with self.assertRaisesRegex(RuntimeError, 'SYNTHETIC_PUBLICATION_FAILURE'):
                self.feedback(first)
        self.assertEqual(self.state(first), before)

    def test_commit_fault_before_or_after_commit_never_accepts_feedback_twice(self):
        for after_commit in (False, True):
            with self.subTest(after_commit=after_commit):
                self.request_id = 'feedback-commit-fault-' + str(after_commit)
                first = self.single()
                before, original, failures = self.state(first), Session.commit, []
                baseline = self.count_events()
                def commit(db):
                    ready = any(isinstance(row, ShoppingIntent) and row.id == first['id'] and row.version == 2
                        for row in list(db.identity_map.values()))
                    if ready and not failures:
                        failures.append(True)
                        if after_commit:
                            original(db)
                        raise RuntimeError('SYNTHETIC_COMMIT_FAULT')
                    return original(db)
                with patch.object(Session, 'commit', new=commit), self.assertRaisesRegex(RuntimeError, 'SYNTHETIC_COMMIT_FAULT'):
                    self.feedback(first)
                if after_commit:
                    with self.assertRaises(HTTPException) as error:
                        self.feedback(first)
                    self.assertEqual(error.exception.status_code, 409)
                else:
                    self.assertEqual(self.state(first), before)
                    self.feedback(first)
                self.assertEqual(self.count_events(), baseline + 1)

    def test_outfit_feedback_releases_disliked_replacement_lock_and_accepts_empty_results(self):
        first = self.outfit()
        outfit = first['outfits'][0]
        second = mutations.build_mutation_response(self.engine, self.user_id, first['id'], operation='replace',
            expected_version=1, outfit_id=outfit['id'], slot='shoes')
        target = second['outfits'][0]['items']['top']
        result = self.feedback(second, target)
        self.assertEqual(result['version'], 3)
        self.assertEqual(result['outfits'], [])
        with self.sessions() as db:
            row = db.get(ShoppingIntent, first['id'])
            self.assertNotIn('top', row.context_json['replacement']['locked'])
            self.assertEqual(row.context_json['replacement']['locked']['bottom'], outfit['items']['bottom'])
            self.assertIn(target, row.context_json['excluded_product_ids'])
        self.assertEqual(self.count_events(), 1)

    def test_excluded_alias_evidence_and_feed_rules_do_not_change_intent_policy(self):
        first = self.single()
        proposal = self.prepare(first)
        target, finalist = self.target(first), proposal.products[0].product_id
        with self.sessions() as db:
            db.get(Product, target).image_url = db.get(Product, finalist).image_url
            db.commit()
        before = self.state(first)
        with self.assertRaises(mutations.RecommendationSnapshotChanged):
            mutations._publish(self.sessions, self.user_id, proposal)
        self.assertEqual(self.state(first), before)
        with self.sessions() as db:
            db.add(SourceRule(id='feed-only-feedback-rule', source_id='merchant', rule_type='blocked_brand',
                rule_value='Synthetic brand', is_active=1))
            db.commit()
        result = self.feedback(first)
        self.assertTrue(result['products'])
        self.assertNotIn(finalist, {row['product']['id'] for row in result['products']})
        self.assertEqual(self.count_events(), 1)

    def test_missing_unissued_or_exhausted_context_rejects_without_any_side_effect(self):
        first = self.single()
        before = self.state(first)
        with self.assertRaises(HTTPException) as error:
            self.feedback(first, 'never-issued')
        self.assertEqual(error.exception.status_code, 404)
        self.assertEqual(self.state(first), before)
        with self.sessions() as db:
            row = db.get(ShoppingIntent, first['id'])
            row.context_json = {**row.context_json, 'excluded_product_ids': [f'previous-{index}' for index in range(200)]}
            db.commit()
        before = self.state(first)
        with self.assertRaises(HTTPException) as error:
            self.feedback(first)
        self.assertEqual(error.exception.status_code, 400)
        self.assertEqual(self.state(first), before)
        with self.sessions() as db:
            db.delete(db.get(Product, self.target(first)))
            db.commit()
        before = self.state(first)
        with self.assertRaises(HTTPException) as error:
            self.feedback(first)
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(self.state(first), before)


class IntentFeedbackHTTPTests(unittest.TestCase):
    setUp = http_fixtures.IntentHTTPTests.setUp
    create = http_fixtures.IntentHTTPTests.create

    def test_actual_feedback_http_publishes_empty_successor_once_and_get_never_ranks(self):
        first = self.create().json()
        url = f"/shopping-intents/{first['id']}/feedback"
        payload = {'expected_version': 1, 'product_id': self.product.id, 'reason': 'dont_like_color'}
        result = self.client.post(url, headers=self.headers, json=payload)
        self.assertEqual(result.status_code, 200)
        self.assertEqual((result.json()['version'], result.json()['products']), (2, []))
        self.assertEqual(self.client.post(url, headers=self.headers, json=payload).status_code, 409)
        with patch.object(service, 'item_results', side_effect=AssertionError('GET cannot rank')):
            read = self.client.get(f"/shopping-intents/{first['id']}", headers=self.headers)
        self.assertEqual((read.status_code, read.json()['version'], read.json()['products']), (200, 2, []))
        with self.sessions() as db:
            rows = list(db.scalars(select(RecommendationEventV2).where(RecommendationEventV2.user_id == self.user.id)))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].meta_json['intent_version'], 1)

    def test_http_validation_and_missing_current_item_have_no_permanent_effects(self):
        first = self.create().json()
        url = f"/shopping-intents/{first['id']}/feedback"
        common = {'expected_version': 1, 'product_id': self.product.id}
        self.assertEqual(self.client.post(url, headers=self.headers,
            json={**common, 'reason': 'unsupported'}).status_code, 422)
        self.assertEqual(self.client.post(url, headers=self.headers,
            json={**common, 'reason': None, 'expected_version': 9}).status_code, 409)
        self.assertEqual(self.client.post(url, headers=self.headers,
            json={**common, 'reason': None, 'product_id': 'not-issued'}).status_code, 404)
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(RecommendationEventV2)), 0)
            self.assertEqual(db.get(ShoppingIntent, first['id']).version, 1)


if __name__ == '__main__':
    unittest.main()
