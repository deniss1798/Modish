"""Personal catalogue reads rank committed inputs without allocating feed state.

SQLite exercises stale-proposal admission, not database lock contention.
"""
from copy import deepcopy
from dataclasses import FrozenInstanceError, fields, is_dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine, delete, event, select
from sqlalchemy.orm import Session, sessionmaker

from app.api import products as products_api
from app.db import Base
from app.models import (ActivationExposure, ActivationSession, FitProfile, MetricEvent,
    Outfit, Product, ProductImpression, ProductSource, RecommendationCandidatePool,
    RecommendationEventV2, SourceRule, StyleProfile, TasteProfile, User,
    UserProductState, UserRecommendationCache, UserTasteFeature)
from app.services import product_read_session as reads
from app.services.recommendation_budget import RecommendationRankingTimeout
from app.services.recommendation_revision import profile_input_mutation
import test_intent_outfit_create_session as fixtures


TOP_IDS = frozenset(f'good-top-{index}' for index in range(3))
COMPLEMENT_IDS = frozenset(f'good-{role}-{index}' for role in ('bottom', 'shoes') for index in range(3))
PERSONAL_MODELS = (StyleProfile, FitProfile, TasteProfile, UserTasteFeature,
    RecommendationEventV2, UserProductState, ActivationSession, ActivationExposure,
    ProductImpression, RecommendationCandidatePool, UserRecommendationCache,
    MetricEvent, Outfit)


class ProductReadSessionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False)
        with self.sessions() as db:
            self.user_id = fixtures.seed_outfit_catalogue(db)
            db.scalar(select(FitProfile).where(FitProfile.user_id == self.user_id)).budget_max = 6000
            db.commit()

    def search(self, **options):
        return reads.build_product_read_response(self.engine, self.user_id,
            operation='search', q=options.pop('q', 'рубашки'), **options)

    def complements(self, **options):
        return reads.build_product_read_response(self.engine, self.user_id,
            operation='complements', product_id=options.pop('product_id', 'good-top-0'), **options)

    def personal_snapshot(self):
        with self.sessions() as db:
            return (db.get(User, self.user_id).recommendation_revision, {
                model.__tablename__: sorted((deepcopy(dict(row)) for row in
                    db.execute(select(model.__table__)).mappings()), key=lambda row: str(row.get('id', row.get('user_id'))))
                for model in PERSONAL_MODELS})

    def clone_product(self, db, template_id, product_id, **changes):
        original = db.get(Product, template_id)
        values = {column.name: deepcopy(getattr(original, column.name)) for column in Product.__table__.columns}
        values.update(id=product_id, external_id=product_id,
            image_url=f'https://baon.ru/images/{product_id}.jpg',
            product_url=f'https://baon.ru/products/{product_id}')
        values.update(changes)
        product = Product(**values)
        db.add(product)
        return product

    def assert_search(self, result, expected=TOP_IDS):
        self.assertEqual({row['id'] for row in result}, set(expected))
        self.assertTrue(all(row['outfit_slot'] == 'top' for row in result))
        self.assertTrue(all(row['gender_target'] == 'womenswear' for row in result))
        self.assertTrue(all(row['available_sizes'] == ['M'] for row in result))
        self.assertTrue(all(Decimal(str(row['price'])) <= 6000 for row in result))

    def _rank_is_select_only(self, operation):
        name = 'personalized_search' if operation == 'search' else 'complementary_products'
        original, statements = getattr(reads, name), []

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

        before = self.personal_snapshot()
        with patch.object(reads, name, side_effect=rank) as ranking:
            result = self.search() if operation == 'search' else self.complements(limit=12)
        ranking.assert_called_once()
        self.assertEqual(set(statements), {'SELECT'})
        self.assertEqual(self.personal_snapshot(), before)
        if operation == 'search':
            self.assert_search(result)
        else:
            self.assertEqual({row['id'] for row in result}, COMPLEMENT_IDS)
            self.assertEqual([row['outfit_slot'] for row in result], ['bottom', 'shoes'] * 3)
            self.assertTrue(all(Decimal(str(row['price'])) + Decimal('1200.25') <= 6000 for row in result))

    def test_search_ranks_real_matches_select_only_without_personal_writes(self):
        self._rank_is_select_only('search')

    def test_complements_rank_real_missing_roles_select_only_without_personal_writes(self):
        self._rank_is_select_only('complements')

    def test_proposals_are_frozen_detached_values_and_keep_unreturned_anchor_evidence(self):
        def detached(value):
            self.assertFalse(hasattr(value, '_sa_instance_state'))
            if is_dataclass(value):
                self.assertTrue(value.__dataclass_params__.frozen)
                for field in fields(value):
                    detached(getattr(value, field.name))
            elif isinstance(value, tuple):
                for item in value:
                    detached(item)
            else:
                self.assertIsInstance(value, (str, int, float, bool, type(None), datetime))

        request = reads.ProductReadRequest(operation='complements', product_id='good-top-0', limit=12)
        before = self.personal_snapshot()
        proposal = reads._prepare(self.sessions, self.user_id, request)
        detached(proposal)
        with self.assertRaises(FrozenInstanceError):
            proposal.revision += 1
        self.assertTrue(proposal.finalists)
        self.assertNotIn('good-top-0', {item.product_id for item in proposal.finalists})
        self.assertEqual({item.product_id for item in proposal.evidence}, COMPLEMENT_IDS | {'good-top-0'})
        # Anchor evidence must be independently rechecked even though it is not
        # a returned finalist. The late-anchor test below verifies the effect.
        self.assertEqual(self.personal_snapshot(), before)

    def test_http_search_and_complements_release_auth_transaction_before_pipeline(self):
        original = reads.build_product_read_response
        for operation in ('search', 'complements'):
            with self.subTest(operation=operation), self.sessions() as auth_db:
                user = auth_db.get(User, self.user_id)

                def build(bind, user_id, **options):
                    self.assertFalse(auth_db.in_transaction())
                    self.assertEqual(user_id, self.user_id)
                    self.assertEqual(options.get('operation', 'search'), operation)
                    return original(bind, user_id, **options)

                with patch.object(products_api, 'user_from_token', return_value=user), \
                        patch.object(reads, 'build_product_read_response', side_effect=build) as pipeline:
                    if operation == 'search':
                        result = products_api.products_list(limit=50, offset=0, category=None,
                            source=None, brand=None, q='рубашки', db=auth_db, credentials=object())
                        self.assert_search(result)
                    else:
                        result = products_api.product_complements('good-top-0', limit=12,
                            db=auth_db, credentials=None)
                        self.assertEqual({row['id'] for row in result}, COMPLEMENT_IDS)
                pipeline.assert_called_once()
                self.assertFalse(auth_db.in_transaction())

    def _late_price_change(self, operation):
        original, changed = reads._publish, []
        before = self.personal_snapshot()

        def publish(sessions, user_id, proposal):
            if not changed:
                changed.append(proposal.finalists[0].product_id)
                with sessions() as db:
                    db.get(Product, changed[0]).price = Decimal('25000.99')
                    db.commit()
            return original(sessions, user_id, proposal)

        with patch.object(reads, '_publish', side_effect=publish) as publishing:
            result = self.search() if operation == 'search' else self.complements(limit=12)
        self.assertEqual(publishing.call_count, 2)
        self.assertTrue(result, 'The replacement snapshot has independent positive SKUs')
        self.assertEqual({row['id'] for row in result}, (TOP_IDS if operation == 'search' else COMPLEMENT_IDS) - set(changed))
        self.assertEqual(self.personal_snapshot(), before)

    def test_search_finalist_price_change_reranks_without_stale_result(self):
        self._late_price_change('search')

    def test_complement_finalist_price_change_reranks_without_stale_result(self):
        self._late_price_change('complements')

    def test_legacy_complements_preserve_per_item_budget_without_adding_total_budget_gate(self):
        with self.sessions() as db:
            db.delete(db.get(ActivationSession, self.user_id))
            db.execute(delete(Product).where(Product.id.in_(fixtures.BAD_IDS)))
            db.get(Product, 'good-top-0').price = Decimal('5900')
            db.commit()
        before = self.personal_snapshot()
        result = self.complements(limit=12)
        self.assertEqual({row['id'] for row in result}, COMPLEMENT_IDS)
        self.assertTrue(all(Decimal(str(row['price'])) + Decimal('5900') > 6000 for row in result))
        self.assertEqual(self.personal_snapshot(), before)

    def test_anchor_change_reranks_even_when_anchor_is_not_a_finalist(self):
        original, changed = reads._publish, []

        def publish(sessions, user_id, proposal):
            if not changed:
                self.assertEqual({item.product_id for item in proposal.finalists}, COMPLEMENT_IDS)
                changed.append(True)
                with sessions() as db:
                    db.get(Product, 'good-top-0').price = Decimal('5900')
                    db.commit()
            return original(sessions, user_id, proposal)

        with patch.object(reads, '_publish', side_effect=publish) as publishing:
            result = self.complements(limit=12)
        self.assertEqual(publishing.call_count, 2)
        self.assertEqual(result, [], 'Every complement now exceeds the exact anchor-plus-item budget')

    def test_profile_change_reranks_using_the_new_budget(self):
        original, changed = reads._publish, []

        def publish(sessions, user_id, proposal):
            if not changed:
                self.assertEqual({item.product_id for item in proposal.finalists}, TOP_IDS)
                changed.append(True)
                with sessions() as db:
                    with profile_input_mutation(db, user_id):
                        db.scalar(select(FitProfile).where(FitProfile.user_id == user_id)).budget_max = 1250
                    db.commit()
            return original(sessions, user_id, proposal)

        with patch.object(reads, '_publish', side_effect=publish) as publishing:
            result = self.search()
        self.assertEqual(publishing.call_count, 2)
        self.assert_search(result, {'good-top-0'})

    def test_two_conflicts_return_409_without_allocating_personal_state(self):
        original = reads._publish
        before = self.personal_snapshot()

        def publish(sessions, user_id, proposal):
            self.assertTrue(proposal.finalists)
            with sessions() as db:
                db.get(Product, proposal.finalists[0].product_id).price += Decimal('0.01')
                db.commit()
            return original(sessions, user_id, proposal)

        with patch.object(reads, '_publish', side_effect=publish) as publishing, self.assertRaises(HTTPException) as caught:
            self.search()
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(publishing.call_count, 2)
        self.assertEqual(self.personal_snapshot(), before)

    def test_ranking_deadline_returns_409_without_publishing_or_personal_writes(self):
        before = self.personal_snapshot()
        with patch.object(reads, 'check_ranking_budget', side_effect=RecommendationRankingTimeout()), \
                patch.object(reads, '_publish', wraps=reads._publish) as publishing, \
                self.assertRaises(HTTPException) as caught:
            self.search()
        self.assertEqual(caught.exception.status_code, 409)
        publishing.assert_not_called()
        self.assertEqual(self.personal_snapshot(), before)

    def test_search_preserves_literal_category_brand_and_valid_source_filters(self):
        with self.sessions() as db:
            db.add(ProductSource(id='read-befree', code='befree', name='Befree', network='direct'))
            db.flush()
            self.clone_product(db, 'good-top-0', 'filtered-befree-top', source='befree',
                source_id='read-befree', brand='Independent source brand',
                image_url='https://befree.ru/images/filtered-top.jpg',
                product_url='https://befree.ru/products/filtered-top')
            db.commit()
        self.assert_search(self.search(source='baon', category='рубашки', brand='outfit'))
        self.assert_search(self.search(source='befree', brand='source'), {'filtered-befree-top'})
        for options in ({'q': 'несуществующийбуквальныйзапрос'}, {'category': 'брюки'},
                {'brand': 'no-such-brand'}, {'source': 'no-such-source'}):
            with self.subTest(options=options):
                self.assertEqual(self.search(**options), [])

    def test_search_page_cap_and_offset_keep_literal_rank_order(self):
        extra_ids = {f'page-top-{index:03d}' for index in range(205)}
        with self.sessions() as db:
            for product_id in sorted(extra_ids):
                self.clone_product(db, 'good-top-0', product_id)
            db.commit()
        # All admitted tops share exactly the same scoring evidence. Existing
        # search resolves score ties by descending SKU, independently of SQL
        # creation order; the page cap applies after literal-query ranking.
        ordered = sorted(TOP_IDS | extra_ids, reverse=True)
        first = self.search(limit=999)
        self.assertEqual([row['id'] for row in first], ordered[:200])
        self.assertEqual([row['id'] for row in self.search(limit=50, offset=199)], ordered[199:249])
        self.assertEqual(self.search(limit=50, offset=5000), [])
        self.assertEqual([row['id'] for row in self.search(limit=0)], ordered[:1])

    def test_search_does_not_add_feed_hidden_or_recent_reaction_exclusions(self):
        now = datetime.now(timezone.utc)
        with self.sessions() as db:
            db.add(UserProductState(id='search-hidden-top', user_id=self.user_id,
                product_id='good-top-0', hidden_until=now + timedelta(days=30)))
            db.add(RecommendationEventV2(id='search-liked-top', user_id=self.user_id,
                product_id='good-top-1', event_type='like', event_weight=1,
                meta_json={'surface': 'feed'}, created_at=now))
            db.get(ActivationSession, self.user_id).swipes_json = {'good-top-2': 'dislike'}
            db.commit()
        before = self.personal_snapshot()
        self.assert_search(self.search())
        self.assertEqual(self.personal_snapshot(), before)

    def test_new_source_rule_after_prepare_reranks_instead_of_returning_rejected_rows(self):
        original, changed = reads._publish, []

        def publish(sessions, user_id, proposal):
            if not changed:
                self.assertTrue(proposal.finalists)
                changed.append(True)
                with sessions() as db:
                    db.add(SourceRule(id='late-read-blocked-brand', source_id='outfit-merchant',
                        rule_type='blocked_brand', rule_value='Synthetic outfit brand', is_active=1))
                    db.commit()
            return original(sessions, user_id, proposal)

        with patch.object(reads, '_publish', side_effect=publish) as publishing:
            result = self.search()
        self.assertEqual(publishing.call_count, 2)
        self.assertEqual(result, [])

    def test_excluded_swipe_alias_change_invalidates_complement_proposal(self):
        with self.sessions() as db:
            db.get(ActivationSession, self.user_id).swipes_json = {'bad-size': 'dislike'}
            db.commit()
        original, changed = reads._publish, []

        def publish(sessions, user_id, proposal):
            if not changed:
                selected = next(item.product_id for item in proposal.finalists
                    if item.product_id.startswith('good-bottom-'))
                changed.append(selected)
                with sessions() as db:
                    # Product is already rejected by SKU. Only its identity
                    # evidence changes, so owner/profile revisions stay equal.
                    db.get(Product, 'bad-size').image_url = db.get(Product, selected).image_url
                    db.commit()
            return original(sessions, user_id, proposal)

        with patch.object(reads, '_publish', side_effect=publish) as publishing:
            result = self.complements(limit=12)
        self.assertEqual(publishing.call_count, 2)
        self.assertEqual({row['id'] for row in result}, COMPLEMENT_IDS - set(changed))


if __name__ == '__main__':
    unittest.main()
