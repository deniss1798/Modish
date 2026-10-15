"""Atomic existing-version edits; SQLite does not prove PostgreSQL locking."""
from dataclasses import FrozenInstanceError
from datetime import timedelta
from decimal import Decimal
import json
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import event, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.api import shopping_intents as api
from app.models import (FitProfile, MetricEvent, Product, RecommendationEventV2,
    ShoppingIntent, SourceRule, TasteProfile, User, UserTasteFeature)
from app.schemas.shopping_intent import IntentOutfitReplace, IntentRefineRequest
from app.services import intent_mutation_session as mutations
from app.services import intent_session as creates, shopping_intent_service as service
from app.services.recommendation_revision import profile_input_mutation
import test_intent_session as fixtures


class IntentMutationSessionTests(unittest.TestCase):
    setUp = fixtures.IntentSessionTests.setUp
    product = fixtures.IntentSessionTests.product
    metrics = fixtures.IntentSessionTests.metrics

    def single(self):
        with self.sessions() as db:
            if db.get(Product, 'cheap-trousers') is None:
                self.product(db, 'cheap-trousers', price=Decimal('4500.99'))
            db.commit()
        return creates.build_intent_response(self.engine, self.user_id,
            text=self.text, request_id=self.request_id)

    def outfit(self, *, alternative=True):
        with self.sessions() as db:
            self.product(db, 'top', title='Женская рубашка', category='рубашки', price=Decimal('1000.10'))
            self.product(db, 'shoe', title='Женские лоферы', category='обувь', price=Decimal('2000.20'),
                available_sizes=['38'], feed_raw_json={'size_system': 'EU'})
            if alternative:
                self.product(db, 'other-shoe', title='Женские лоферы', category='обувь', price=Decimal('2100.30'),
                    available_sizes=['38'], feed_raw_json={'size_system': 'EU'})
            user = db.get(User, self.user_id)
            row = service.create_intent(db, user, text='Образ до 20000 без платья', request_id=self.request_id)
            result = service.intent_response(db, user, row, remember=True)
            db.commit()
        self.assertTrue(result['outfits'])
        return result

    def state(self, intent_id):
        """Include every personal/metric side effect, without diagnostics data."""
        with self.sessions() as db:
            row = db.get(ShoppingIntent, intent_id)
            values = {'intent': {column.name: getattr(row, column.name) for column in row.__table__.columns},
                'revision': db.get(User, self.user_id).recommendation_revision}
            for model in (TasteProfile, UserTasteFeature, RecommendationEventV2, MetricEvent):
                values[model.__tablename__] = [
                    {column.name: getattr(record, column.name) for column in model.__table__.columns}
                    for record in db.scalars(select(model).where(model.user_id == self.user_id).order_by(model.id))]
            return json.dumps(values, sort_keys=True, default=str)

    def mutate(self, first, **options):
        return mutations.build_mutation_response(self.engine, self.user_id, first['id'],
            operation=options.pop('operation', 'refine'), expected_version=options.pop('expected_version', first['version']),
            **options)

    def test_read_only_prepare_freezes_proposal_and_preserves_old_version_until_publish(self):
        first = self.single()
        before, statements = self.state(first['id']), []
        original = mutations.item_results
        def rank(db, *args, **kwargs):
            self.assertTrue(db.info['recommendation_read_only'])
            self.assertNotIn('_recommendation_owner_mutations', db.info)
            def record(_connection, _cursor, sql, *_args):
                statements.append(sql.strip().split()[0].upper())
            event.listen(self.engine, 'before_cursor_execute', record)
            try:
                return original(db, *args, **kwargs)
            finally:
                event.remove(self.engine, 'before_cursor_execute', record)
        request = mutations.MutationRequest('refine', 1, text='Дешевле')
        with patch.object(mutations, 'item_results', side_effect=rank):
            proposal = mutations._prepare(self.sessions, self.user_id, first['id'], request)
        self.assertEqual(self.state(first['id']), before)
        self.assertEqual(set(statements), {'SELECT'})
        with self.assertRaises(FrozenInstanceError):
            proposal.revision += 1
        result = mutations._publish(self.sessions, self.user_id, proposal)
        self.assertEqual(result['version'], 2)
        self.assertEqual(result['constraints']['budget_max'], 5600)
        self.assertEqual([row['product']['id'] for row in result['products']], ['cheap-trousers'])
        self.assertEqual(self.metrics('intent_updated'), 1)
        self.assertEqual(self.metrics(), 2)

    def test_reference_price_is_a_dependency_even_when_reference_is_not_a_finalist(self):
        first = self.single()
        reference_id = next(row['product']['id'] for row in first['products'] if row['product']['id'] != 'cheap-trousers')
        request = mutations.MutationRequest('refine', 1, text='Дешевле', reference_product_id=reference_id)
        proposal = mutations._prepare(self.sessions, self.user_id, first['id'], request)
        self.assertNotIn(reference_id, {row.product_id for row in proposal.products})
        self.assertIn(reference_id, {row.product_id for row in proposal.evidence})
        with self.sessions() as db:
            db.get(Product, reference_id).price = Decimal('5000.99')
            db.commit()
        before = self.state(first['id'])
        with self.assertRaises(mutations.RecommendationSnapshotChanged):
            mutations._publish(self.sessions, self.user_id, proposal)
        self.assertEqual(self.state(first['id']), before)
        result = self.mutate(first, text='Дешевле', reference_product_id=reference_id)
        self.assertEqual(result['constraints']['budget_max'], 4000)
        self.assertEqual(result['products'], [])

    def test_http_refine_releases_auth_session_before_ranking(self):
        first = self.single()
        with self.sessions() as db, patch.object(api, 'user_from_token', return_value=db.get(User, self.user_id)):
            result = api.refine(first['id'], IntentRefineRequest(text='Дешевле', expected_version=1), db=db, credentials=None)
            self.assertFalse(db.in_transaction())
            self.assertEqual(len(db.identity_map), 0)
        self.assertEqual(result['version'], 2)

    def test_missing_reference_requires_refresh_without_ranking_or_any_mutation(self):
        first = self.single()
        reference_id = first['products'][0]['product']['id']
        with self.sessions() as db:
            db.delete(db.get(Product, reference_id))
            db.commit()
        before = self.state(first['id'])
        with patch.object(mutations, 'item_results', side_effect=AssertionError('Missing reference cannot rank')):
            with self.assertRaises(HTTPException) as error:
                self.mutate(first, text='Дешевле', reference_product_id=reference_id)
        self.assertEqual((error.exception.status_code, error.exception.detail),
            (409, 'Вещь уже недоступна. Обновите результат.'))
        self.assertEqual(self.state(first['id']), before)

    def test_http_replace_releases_auth_session_before_ranking(self):
        first = self.outfit()
        outfit = first['outfits'][0]
        with self.sessions() as db, patch.object(api, 'user_from_token', return_value=db.get(User, self.user_id)):
            result = api.replace_outfit(first['id'], outfit['id'], IntentOutfitReplace(slot='shoes', expected_version=1),
                db=db, credentials=None)
            self.assertFalse(db.in_transaction())
            self.assertEqual(len(db.identity_map), 0)
        self.assertEqual(result['version'], 2)

    def test_replacement_preserves_locked_items_sizes_and_kopecks(self):
        first = self.outfit()
        before = first['outfits'][0]
        request = mutations.MutationRequest('replace', 1, outfit_id=before['id'], slot='shoes')
        proposal = mutations._prepare(self.sessions, self.user_id, first['id'], request)
        self.assertLessEqual(set(before['items'].values()), {row.product_id for row in proposal.evidence})
        after = mutations._publish(self.sessions, self.user_id, proposal)['outfits'][0]
        self.assertNotEqual(after['items']['shoes'], before['items']['shoes'])
        self.assertNotEqual(after['id'], before['id'])
        for slot in ('top', 'bottom'):
            self.assertEqual(after['items'][slot], before['items'][slot])
            self.assertEqual(after['size_matches'][slot], before['size_matches'][slot])
        total = sum(Decimal(str(row['price'])) for row in after['products'].values())
        self.assertEqual(Decimal(str(after['total_price'])), total)
        self.assertEqual(self.metrics('outfit_item_replaced'), 1)
        self.assertEqual(self.metrics('intent_updated'), 1)
        self.assertEqual(self.metrics(), 2)

    def test_no_replacement_rolls_back_every_existing_version_side_effect(self):
        first = self.outfit(alternative=False)
        before = self.state(first['id'])
        with self.assertRaises(HTTPException) as error:
            self.mutate(first, operation='replace', outfit_id=first['outfits'][0]['id'], slot='shoes')
        self.assertEqual((error.exception.status_code, error.exception.detail), (400, mutations.NO_REPLACEMENT_MESSAGE))
        self.assertEqual(self.state(first['id']), before)

    def test_text_replacement_uses_same_atomic_plan_and_metrics(self):
        first = self.outfit()
        before = first['outfits'][0]
        result = self.mutate(first, text='замени обувь', reference_outfit_id=before['id'])
        self.assertEqual(result['version'], 2)
        self.assertNotEqual(result['outfits'][0]['items']['shoes'], before['items']['shoes'])
        self.assertEqual(self.metrics('intent_updated'), 1)
        self.assertEqual(self.metrics('outfit_item_replaced'), 1)

    def test_personal_conflict_reranks_once_without_partial_intent_metrics(self):
        first = self.single()
        original, proposals = mutations._publish, []
        def publish(sessions, user_id, proposal):
            proposals.append(proposal)
            if len(proposals) == 1:
                with self.sessions() as db:
                    with profile_input_mutation(db, self.user_id):
                        db.scalar(select(FitProfile).where(FitProfile.user_id == self.user_id)).budget_max = 500
                        self.product(db, 'new-cheap', price=Decimal('399.99'))
                    db.commit()
            return original(sessions, user_id, proposal)
        with patch.object(mutations, '_publish', side_effect=publish):
            result = self.mutate(first, text='Дешевле')
        self.assertEqual(len(proposals), 2)
        self.assertEqual([row['product']['id'] for row in result['products']], ['new-cheap'])
        self.assertEqual(self.metrics('intent_updated'), 1)
        self.assertEqual(self.metrics(), 2)

    def test_two_catalogue_conflicts_preserve_original_version_and_metrics(self):
        first = self.single()
        before, original, calls = self.state(first['id']), mutations._publish, []
        def publish(sessions, user_id, proposal):
            calls.append(proposal)
            with self.sessions() as db:
                row = db.get(Product, proposal.products[0].product_id)
                row.price += Decimal('0.01')
                db.commit()
            return original(sessions, user_id, proposal)
        with patch.object(mutations, '_publish', side_effect=publish), self.assertRaises(HTTPException) as error:
            self.mutate(first, text='Дешевле')
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.state(first['id']), before)

    def test_competing_publisher_wins_once_and_old_expected_version_never_reranks(self):
        first = self.single()
        request = mutations.MutationRequest('refine', 1, text='Дешевле')
        left = mutations._prepare(self.sessions, self.user_id, first['id'], request)
        right = mutations._prepare(self.sessions, self.user_id, first['id'], request)
        mutations._publish(self.sessions, self.user_id, left)
        before = self.state(first['id'])
        with self.assertRaises(service.IntentError) as error:
            mutations._publish(self.sessions, self.user_id, right)
        self.assertEqual(error.exception.status, 409)
        with patch.object(mutations, 'item_results', side_effect=AssertionError('Stale version cannot rank')):
            with self.assertRaises(HTTPException) as retry:
                self.mutate(first, text='Дешевле')
        self.assertEqual(retry.exception.status_code, 409)
        self.assertEqual(self.state(first['id']), before)

    def test_locked_product_change_is_checked_and_rejection_preserves_version(self):
        first = self.outfit()
        outfit = first['outfits'][0]
        request = mutations.MutationRequest('replace', 1, outfit_id=outfit['id'], slot='shoes')
        proposal = mutations._prepare(self.sessions, self.user_id, first['id'], request)
        with self.sessions() as db:
            db.get(Product, outfit['items']['top']).is_available = 0
            db.commit()
        before = self.state(first['id'])
        with self.assertRaises(mutations.RecommendationSnapshotChanged):
            mutations._publish(self.sessions, self.user_id, proposal)
        with self.assertRaises(HTTPException) as error:
            self.mutate(first, operation='replace', outfit_id=outfit['id'], slot='shoes')
        self.assertEqual(error.exception.status_code, 400)
        self.assertEqual(self.state(first['id']), before)

    def test_exclusion_side_identity_change_is_rejected_before_owner_lock(self):
        first = self.single()
        with self.sessions() as db:
            row = db.get(ShoppingIntent, first['id'])
            row.context_json = {**row.context_json, 'excluded_product_ids': ['z-good-000']}
            db.commit()
        proposal = mutations._prepare(self.sessions, self.user_id, first['id'],
            mutations.MutationRequest('refine', 1, text='Дешевле'))
        with self.sessions() as db:
            db.get(Product, 'z-good-000').image_url = db.get(Product, 'cheap-trousers').image_url
            db.commit()
        before = self.state(first['id'])
        with patch.object(mutations, 'owner_mutation', side_effect=AssertionError('Exclusions checked first')):
            with self.assertRaises(mutations.RecommendationSnapshotChanged):
                mutations._publish(self.sessions, self.user_id, proposal)
        self.assertEqual(self.state(first['id']), before)

    def test_commit_fault_before_or_after_commit_has_no_automatic_second_mutation(self):
        for after_commit in (False, True):
            with self.subTest(after_commit=after_commit):
                self.request_id = 'mutation-commit-fault-' + str(after_commit)
                first = self.single()
                before, original, failed = self.state(first['id']), Session.commit, []
                generated_before, updated_before = self.metrics(), self.metrics('intent_updated')
                def commit(db):
                    edited = any(isinstance(row, ShoppingIntent) and row.id == first['id'] and row.version == 2
                        for row in list(db.identity_map.values()))
                    if edited and not failed:
                        failed.append(True)
                        if after_commit:
                            original(db)
                        raise RuntimeError('SYNTHETIC_COMMIT_FAULT')
                    return original(db)
                with patch.object(Session, 'commit', new=commit), self.assertRaisesRegex(RuntimeError, 'SYNTHETIC_COMMIT_FAULT'):
                    self.mutate(first, text='Дешевле')
                if after_commit:
                    with self.assertRaises(HTTPException) as error:
                        self.mutate(first, text='Дешевле')
                    self.assertEqual(error.exception.status_code, 409)
                    with self.sessions() as db:
                        user = db.get(User, self.user_id)
                        self.assertEqual(service.issued_intent_response(db, user,
                            service.get_intent(db, user, first['id']))['version'], 2)
                else:
                    self.assertEqual(self.state(first['id']), before)
                    self.assertEqual(self.mutate(first, text='Дешевле')['version'], 2)
                self.assertEqual(self.metrics(), generated_before + 1)
                self.assertEqual(self.metrics('intent_updated'), updated_before + 1)

    def test_ranking_deadline_returns_once_without_accepting_any_part_of_edit(self):
        first = self.single()
        before = self.state(first['id'])
        with patch.object(mutations, 'check_ranking_budget', side_effect=mutations.RecommendationRankingTimeout), \
                patch.object(mutations, 'item_results', wraps=mutations.item_results) as ranking, \
                patch.object(mutations, '_publish', side_effect=AssertionError('Timed out proposal cannot publish')):
            with self.assertRaises(HTTPException) as error:
                self.mutate(first, text='Дешевле')
        self.assertEqual((error.exception.status_code, error.exception.detail), (409, mutations.TIMEOUT_MESSAGE))
        self.assertEqual(ranking.call_count, 1)
        self.assertEqual(self.state(first['id']), before)

    def test_sql_timeout_during_prepare_does_not_launch_second_heavy_attempt(self):
        first = self.single()
        before = self.state(first['id'])
        class Cancelled(Exception):
            sqlstate = '57014'
        failure = DBAPIError(None, None, Cancelled(), False)
        with patch.object(mutations, '_prepare', side_effect=failure) as prepare:
            with self.assertRaises(HTTPException) as error:
                self.mutate(first, text='Дешевле')
        self.assertEqual((error.exception.status_code, error.exception.detail), (409, mutations.TIMEOUT_MESSAGE))
        self.assertEqual(prepare.call_count, 1)
        self.assertEqual(self.state(first['id']), before)

    def test_short_publish_timeout_keeps_bounded_retry_and_no_partial_edit(self):
        first = self.single()
        before = self.state(first['id'])
        class Cancelled(Exception):
            sqlstate = '57014'
        failure = DBAPIError(None, None, Cancelled(), False)
        with patch.object(mutations, '_publish', side_effect=failure) as publish, \
                patch.object(mutations, '_prepare', wraps=mutations._prepare) as prepare:
            with self.assertRaises(HTTPException) as error:
                self.mutate(first, text='Дешевле')
        self.assertEqual((error.exception.status_code, error.exception.detail), (409, mutations.CONFLICT_MESSAGE))
        self.assertEqual((prepare.call_count, publish.call_count), (2, 2))
        self.assertEqual(self.state(first['id']), before)

    def test_missing_taste_does_not_initialize_or_publish(self):
        first = self.single()
        with self.sessions() as db:
            db.delete(db.scalar(select(TasteProfile).where(TasteProfile.user_id == self.user_id)))
            db.commit()
        before = self.state(first['id'])
        with self.assertRaises(HTTPException) as error:
            self.mutate(first, text='Дешевле')
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(self.state(first['id']), before)

    def test_expired_version_does_not_publish(self):
        first = self.single()
        with self.sessions() as db:
            db.get(ShoppingIntent, first['id']).expires_at = mutations._now() - timedelta(seconds=1)
            db.commit()
        before = self.state(first['id'])
        with self.assertRaises(HTTPException) as error:
            self.mutate(first, text='Дешевле')
        self.assertEqual(error.exception.status_code, 410)
        self.assertEqual(self.state(first['id']), before)

    def test_unresolved_edit_and_feed_only_source_rules_preserve_intent_policy(self):
        first = self.single()
        with self.sessions() as db:
            db.add(SourceRule(id='feed-only-rule', source_id='merchant', rule_type='blocked_brand',
                rule_value='Synthetic brand', is_active=1))
            db.commit()
        result = self.mutate(first, text='Дешевле')
        self.assertTrue(result['products'])
        with patch.object(mutations, 'item_results', side_effect=AssertionError('Unresolved edit must not rank')):
            clarified = self.mutate(result, text='из кожи дракона')
        self.assertEqual(clarified['state'], 'needs_clarification')
        self.assertEqual(clarified['products'], [])
        self.assertEqual(clarified['version'], 3)


if __name__ == '__main__':
    unittest.main()
