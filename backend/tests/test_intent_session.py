"""Durable single-item claims and publication races over a fixed SKU oracle.

SQLite proves lifecycle and atomicity, not PostgreSQL row-lock concurrency.
"""
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.api import shopping_intents as api
from app.db import Base
from app.models import ActivationSession, FitProfile, MetricEvent, Product, ProductSource, ShoppingIntent, SourceRule, User
from app.schemas.shopping_intent import IntentCreateRequest
from app.services import activation_service as activation, intent_session as intents, shopping_intent_service as service
from app.services.recommendation_revision import owner_mutation, profile_input_mutation


class IntentSessionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False)
        self.user_id = 'intent-session-owner'
        self.request_id = 'stable-intent-session-key'
        self.text = 'Брюки до 7000'
        self.good = {f'z-good-{index:03}' for index in range(12)}
        self.bad = {'a-bad-budget', 'a-bad-size', 'a-bad-gender', 'a-bad-kids', 'a-bad-image'}
        with self.sessions() as db:
            user = User(id=self.user_id, email='intent-session@example.test', password_hash='unused')
            db.add(user)
            db.flush()
            session = activation.configure(db, user, gender_target='womenswear', age_group='25-34', height=168,
                clothing_size='48', clothing_size_system='RU', shoe_size='38', shoe_size_system='EU',
                styles=['casual', 'minimalism'], price_segment='mid')
            session.completed_at = datetime.now(timezone.utc)
            db.add(ProductSource(id='merchant', code='oracle-merchant', name='Synthetic merchant', network='direct'))
            for sku in sorted(self.good):
                self.product(db, sku)
            for sku, changes in (
                    ('a-bad-budget', {'price': Decimal('7000.99')}),
                    ('a-bad-size', {'available_sizes': ['46']}),
                    ('a-bad-gender', {'gender_target': 'menswear'}),
                    ('a-bad-kids', {'title': 'Брюки для девочек'}),
                    ('a-bad-image', {'image_url': 'http://localhost./image.jpg'})):
                self.product(db, sku, **changes)
            db.commit()

    def product(self, db, sku, **changes):
        fields = dict(id=sku, external_id=sku, source='baon', source_id='merchant', title='Женские брюки прямые',
            brand='Synthetic brand', category='брюки', price=Decimal('6000.50'), currency='RUB',
            gender_target='womenswear', available_sizes=['48'], feed_raw_json={'size_system': 'RU'},
            image_url=f'https://baon.ru/images/{sku}.jpg', product_url=f'https://baon.ru/products/{sku}',
            is_active=1, is_available=1, is_deleted_from_feed=0, availability_status='in_stock',
            last_seen_in_feed_at=datetime.now(timezone.utc))
        fields.update(changes)
        row = Product(**fields)
        db.add(row)
        return row

    def build(self, **options):
        return intents.build_intent_response(self.engine, self.user_id,
            text=options.pop('text', self.text), request_id=options.pop('request_id', self.request_id), **options)

    def admit(self):
        return intents._admit(self.sessions, self.user_id, text=self.text, request_id=self.request_id)

    def metrics(self, name='intent_results_generated'):
        with self.sessions() as db:
            return db.scalar(select(func.count()).select_from(MetricEvent).where(MetricEvent.name == name))

    def intent(self):
        with self.sessions() as db:
            return db.scalar(select(ShoppingIntent).where(ShoppingIntent.request_id == self.request_id))

    def expire_lease(self, claim):
        with self.sessions() as db:
            with owner_mutation(db, self.user_id):
                row = db.get(ShoppingIntent, claim.intent_id)
                generation = row.context_json[service.GENERATION_KEY]
                row.context_json = {**row.context_json, service.GENERATION_KEY: {
                    **generation, 'lease_until': (intents._now() - timedelta(seconds=1)).isoformat()}}
            db.commit()

    def assert_oracle(self, response):
        ids = [row['product']['id'] for row in response['products']]
        self.assertTrue(ids)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertLessEqual(set(ids), self.good)
        self.assertTrue(set(ids).isdisjoint(self.bad))

    def test_rank_is_read_only_and_normal_create_publishes_once(self):
        original, statements = intents.item_results, []
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
        with patch.object(intents, 'item_results', side_effect=rank) as ranking:
            result = self.build()
        self.assertEqual(ranking.call_count, 1)
        self.assertEqual(set(statements), {'SELECT'})
        self.assertEqual(len(result['products']), 10)
        self.assert_oracle(result)
        self.assertEqual(self.metrics(), 1)
        self.assertEqual(self.metrics('intent_created'), 1)
        self.assertEqual(self.metrics('intent_parsed'), 1)
        row = self.intent()
        self.assertEqual(row.context_json['result_version'], 1)
        self.assertEqual(row.context_json[service.GENERATION_KEY]['state'], 'ready')

    def test_pending_is_committed_and_http_get_create_refine_never_scan_it(self):
        admission = self.admit()
        row = self.intent()
        self.assertTrue(service.generation_pending(row))
        self.assertNotIn('result_version', row.context_json)
        with patch.object(intents, 'item_results', side_effect=AssertionError('Pending must not rank')):
            with self.assertRaises(HTTPException) as error:
                self.build()
            self.assertEqual(error.exception.status_code, 409)
            self.assertEqual(error.exception.headers, {'Retry-After': '2'})
            with self.sessions() as db, patch.object(api, 'user_from_token', return_value=db.get(User, self.user_id)):
                with self.assertRaises(HTTPException) as get_error:
                    api.read(admission.intent_id, db=db, credentials=None)
                self.assertEqual(get_error.exception.status_code, 409)
            with self.sessions() as db:
                user = db.get(User, self.user_id)
                with self.assertRaises(service.IntentError):
                    service.refine_intent(db, user, admission.intent_id, text='Дешевле', expected_version=1)
        self.assertEqual(self.metrics(), 0)
        self.assertEqual(self.intent().version, 1)

    def test_same_key_different_text_is_rejected_without_replacing_pending(self):
        claim = self.admit().claim
        with self.assertRaises(HTTPException) as error:
            self.build(text='Платье до 8000')
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(self.intent().context_json[service.GENERATION_KEY]['claim_id'], claim.claim_id)
        self.assertEqual(self.metrics('intent_created'), 1)

    def test_interrupted_pending_recovers_generation_and_fences_old_worker(self):
        old = self.admit().claim
        proposal = intents._prepare(self.sessions, self.user_id, old)
        with self.assertRaises(FrozenInstanceError):
            proposal.revision += 1
        self.expire_lease(old)
        result = self.build()
        self.assert_oracle(result)
        row = self.intent()
        self.assertEqual(row.version, old.version)
        self.assertEqual(row.context_json[service.GENERATION_KEY]['generation'], old.generation + 1)
        before = row.context_json.copy()
        with self.assertRaises(service.IntentError):
            intents._publish(self.sessions, self.user_id, proposal)
        intents._release_claim(self.sessions, self.user_id, old)
        self.assertEqual(self.intent().context_json, before)
        self.assertEqual(self.metrics(), 1)

    def test_ready_post_replay_and_get_keep_issued_ids_without_ranking(self):
        first = self.build()
        with self.sessions() as db:
            self.product(db, 'new-higher-rank', price=Decimal('400'))
            db.get(Product, first['products'][0]['product']['id']).is_available = 0
            db.commit()
        with patch.object(intents, 'item_results', side_effect=AssertionError('Replay must not rank')), \
                patch.object(service, 'item_results', side_effect=AssertionError('GET must not rank')):
            replay = self.build()
            with self.sessions() as db:
                user = db.get(User, self.user_id)
                get = service.issued_intent_response(db, user, service.get_intent(db, user, first['id']))
        expected = [row['product']['id'] for row in first['products'][1:]]
        self.assertEqual([row['product']['id'] for row in replay['products']], expected)
        self.assertEqual([row['product']['id'] for row in get['products']], expected)
        self.assertEqual(self.metrics(), 1)

    def test_http_create_closes_auth_session_and_preserves_response_contract(self):
        with self.sessions() as db:
            user = db.get(User, self.user_id)
            with patch.object(api, 'user_from_token', return_value=user):
                result = api.create(IntentCreateRequest(text=self.text, request_id=self.request_id), db=db, credentials=None)
            self.assertFalse(db.in_transaction())
            self.assertEqual(len(db.identity_map), 0)
        self.assert_oracle(result)
        self.assertEqual(result['state'], 'ready')

    def test_personal_conflict_retries_outside_lock_and_uses_new_budget(self):
        original, calls = intents._publish, []
        def publish(sessions, user_id, proposal):
            calls.append(proposal)
            if len(calls) == 1:
                with self.sessions() as db:
                    with profile_input_mutation(db, self.user_id):
                        db.scalar(select(FitProfile).where(FitProfile.user_id == self.user_id)).budget_max = 500
                        self.product(db, 'new-cheap', price=Decimal('499.99'))
                    db.commit()
                self.good.add('new-cheap')
            return original(sessions, user_id, proposal)
        with patch.object(intents, '_publish', side_effect=publish):
            result = self.build()
        self.assertEqual(len(calls), 2)
        self.assertEqual([row['product']['id'] for row in result['products']], ['new-cheap'])
        self.assertEqual(self.metrics(), 1)

    def test_catalogue_conflict_retries_without_old_price_or_partial_metrics(self):
        original, changed = intents._publish, []
        def publish(sessions, user_id, proposal):
            if not changed:
                changed.append(proposal.finalists[0].product_id)
                with self.sessions() as db:
                    db.get(Product, changed[0]).price = Decimal('7000.99')
                    db.commit()
            return original(sessions, user_id, proposal)
        with patch.object(intents, '_publish', side_effect=publish) as publishing:
            result = self.build()
        self.assertEqual(publishing.call_count, 2)
        self.assertNotIn(changed[0], {row['product']['id'] for row in result['products']})
        self.assertEqual(self.metrics(), 1)

    def test_two_conflicts_release_lease_without_result_then_same_key_recovers(self):
        original = intents._publish
        def publish(sessions, user_id, proposal):
            with self.sessions() as db:
                with profile_input_mutation(db, self.user_id):
                    db.scalar(select(FitProfile).where(FitProfile.user_id == self.user_id)).budget_max -= 1
                db.commit()
            return original(sessions, user_id, proposal)
        with patch.object(intents, '_publish', side_effect=publish) as publishing, self.assertRaises(HTTPException) as error:
            self.build()
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(publishing.call_count, 2)
        row = self.intent()
        self.assertTrue(service.generation_pending(row))
        self.assertNotIn('result_version', row.context_json)
        self.assertLessEqual(datetime.fromisoformat(row.context_json[service.GENERATION_KEY]['lease_until']), intents._now())
        self.assertEqual(self.metrics(), 0)
        self.assert_oracle(self.build())
        self.assertEqual(self.metrics(), 1)

    def test_intent_version_change_is_not_retried_or_overwritten(self):
        original = intents._publish
        def publish(sessions, user_id, proposal):
            with self.sessions() as db:
                with owner_mutation(db, self.user_id):
                    db.get(ShoppingIntent, proposal.claim.intent_id).version += 1
                db.commit()
            return original(sessions, user_id, proposal)
        with patch.object(intents, '_publish', side_effect=publish), patch.object(
                intents, '_prepare', wraps=intents._prepare) as prepare, self.assertRaises(HTTPException) as error:
            self.build()
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(prepare.call_count, 1)
        self.assertEqual(self.intent().version, 2)
        self.assertEqual(self.metrics(), 0)

    def test_commit_fault_before_or_after_commit_never_duplicates_publication(self):
        for after_commit in (False, True):
            with self.subTest(after_commit=after_commit):
                self.request_id = 'commit-fault-key-' + str(after_commit)
                original, failed = Session.commit, []
                def commit(db):
                    ready = any(isinstance(row, ShoppingIntent) and
                        (row.context_json or {}).get(service.GENERATION_KEY, {}).get('state') == 'ready'
                        for row in list(db.identity_map.values()))
                    if ready and not failed:
                        failed.append(True)
                        if after_commit:
                            original(db)
                        raise RuntimeError('SYNTHETIC_COMMIT_FAULT')
                    return original(db)
                before = self.metrics()
                with patch.object(Session, 'commit', new=commit), self.assertRaisesRegex(RuntimeError, 'SYNTHETIC_COMMIT_FAULT'):
                    self.build()
                self.assertEqual(self.metrics(), before + int(after_commit))
                with patch.object(intents, 'item_results', wraps=intents.item_results) as ranking:
                    result = self.build()
                self.assert_oracle(result)
                self.assertEqual(ranking.call_count, 0 if after_commit else 1)
                self.assertEqual(self.metrics(), before + 1)

    def test_unknown_uncomputed_get_never_silently_scans_catalogue(self):
        with self.sessions() as db:
            user = db.get(User, self.user_id)
            row = service.create_intent(db, user, text=self.text, request_id=self.request_id)
            db.commit()
            with patch.object(service, 'item_results', side_effect=AssertionError('GET must not rank')):
                with self.assertRaises(service.IntentError) as error:
                    service.issued_intent_response(db, user, row)
            self.assertEqual(error.exception.status, 409)
        self.assert_oracle(self.build())

    def test_unresolved_and_empty_results_are_published_once_without_fake_pending(self):
        with patch.object(intents, 'item_results', side_effect=AssertionError('Clarification must not rank')):
            result = self.build(text='Брюки из кожи дракона')
        self.assertEqual(result['state'], 'needs_clarification')
        self.assertEqual(self.metrics(), 1)
        self.request_id = 'empty-intent-result-key'
        result = self.build(text='Платье до 8000')
        self.assertEqual(result['state'], 'ready')
        self.assertEqual(result['products'], [])
        with patch.object(intents, 'item_results', side_effect=AssertionError('Empty replay must not rank')):
            self.assertEqual(self.build(text='Платье до 8000')['products'], [])
        self.assertEqual(self.metrics(), 2)

    def test_current_intent_policy_does_not_add_feed_source_rules(self):
        with self.sessions() as db:
            db.add(SourceRule(id='feed-only-rule', source_id='merchant', rule_type='blocked_brand',
                rule_value='Synthetic brand', is_active=1))
            db.commit()
        self.assert_oracle(self.build())

    def test_excluded_side_identity_change_conflicts_before_owner_publication(self):
        claim = self.admit().claim
        with self.sessions() as db:
            row = db.get(ShoppingIntent, claim.intent_id)
            row.context_json = {**row.context_json, 'excluded_product_ids': ['z-good-000']}
            db.commit()
        proposal = intents._prepare(self.sessions, self.user_id, claim)
        finalist = proposal.finalists[0].product_id
        with self.sessions() as db:
            db.get(Product, 'z-good-000').image_url = db.get(Product, finalist).image_url
            db.commit()
        with self.assertRaises(intents.RecommendationSnapshotChanged):
            intents._publish(self.sessions, self.user_id, proposal)
        self.assertEqual(self.metrics(), 0)

    def test_ready_replay_and_http_get_remove_new_hidden_alias_without_ranking(self):
        first = self.build()
        forbidden = first['products'][0]['product']['id']
        with self.sessions() as db:
            hidden = self.product(db, 'already-excluded-later-alias')
            row = db.get(ShoppingIntent, first['id'])
            row.context_json = {**row.context_json, 'excluded_product_ids': [hidden.id]}
            db.flush()
            hidden.image_url = db.get(Product, forbidden).image_url
            db.commit()
        with patch.object(intents, 'item_results', side_effect=AssertionError('Replay must not rank')), \
                patch.object(service, 'item_results', side_effect=AssertionError('GET must not rank')):
            replay = self.build()
            with self.sessions() as db, patch.object(api, 'user_from_token', return_value=db.get(User, self.user_id)):
                read = api.read(first['id'], db=db, credentials=None)
        expected = {row['product']['id'] for row in first['products']} - {forbidden}
        self.assertEqual({row['product']['id'] for row in replay['products']}, expected)
        self.assertEqual({row['product']['id'] for row in read['products']}, expected)
        self.assertEqual(self.metrics(), 1)
