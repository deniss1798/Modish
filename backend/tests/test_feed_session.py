"""Publication races over committed synthetic catalogue inputs.

SQLite verifies transaction boundaries and admission. Actual lock contention is
covered separately by the opt-in PostgreSQL suite, never inferred from SQLite.
"""
from dataclasses import FrozenInstanceError, fields, is_dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import delete, event, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.api import products as products_api, recommendations as recommendations_api
from app.models import ActivationExposure, ActivationSession, FitProfile, Product, ProductImpression, ProductSource, RecommendationCandidatePool, SourceRule, User, UserRecommendationCache
from app.services import feed_session as feeds
from app.services.catalog import stock_freshness
from app.services.catalog_filters import CatalogFilters
from app.services.recommendation_revision import profile_input_mutation
from app.schemas.product_event import EventRequest
from app.services.product_event_service import record_product_event
import test_closed_alpha_first_session as fixtures


class FeedSessionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ClosedAlphaFirstSessionTests()
        scenario = self.fixture.scenario()
        scenario.__enter__()
        self.addCleanup(scenario.__exit__, None, None, None)
        self.fixture.positive_catalogue()
        self.fixture.negative_catalogue()
        self.liked = self.fixture.warm()
        self.db = self.fixture.db
        self.bind = self.db.get_bind()
        self.user_id = self.fixture.user.id
        self.db.commit()
        self.sessions = sessionmaker(bind=self.bind, autoflush=False, expire_on_commit=False)

    def count(self, model):
        with self.sessions() as db:
            return db.scalar(select(func.count()).select_from(model))

    def proposal(self):
        return feeds._prepare(self.sessions, self.user_id, limit=20, source=None,
            scenario='daily', filters=None, exclude_ids=())

    def test_readonly_rank_delivers_real_first_twenty_without_actual_exposures(self):
        statements = []
        original = feeds.generate_feed

        def rank(db, user, **options):
            self.assertTrue(options['read_only'])
            self.assertTrue(db.info['recommendation_read_only'])
            def record(_connection, _cursor, statement, *_args):
                statements.append(statement.strip().split()[0].upper())
            event.listen(self.bind, 'before_cursor_execute', record)
            try:
                return original(db, user, **options)
            finally:
                event.remove(self.bind, 'before_cursor_execute', record)

        with self.sessions() as db:
            revision = db.get(User, self.user_id).recommendation_revision
        with patch.object(feeds, 'generate_feed', side_effect=rank):
            result = feeds.build_feed_response(self.bind, self.user_id, limit=20)
        self.assertEqual(len(result), 20)
        ids = [row['product']['id'] for row in result]
        self.fixture.assert_oracle(ids)
        self.assertTrue(set(ids).isdisjoint(self.liked))
        self.assertEqual(set(statements), {'SELECT'})
        self.assertEqual(self.count(ProductImpression), 20)
        self.assertEqual(self.count(ActivationExposure), 0)
        with self.sessions() as db:
            self.assertEqual(db.get(User, self.user_id).recommendation_revision, revision)
            self.assertEqual(set(db.get(ActivationSession, self.user_id).issued_feed_ids), set(ids))
        for row in result:
            self.assertEqual(set(row), {'product', 'final_score', 'breakdown', 'candidate_sources',
                'algorithm_version', 'ranking_algorithm', 'reason', 'reasons'})
            self.assertEqual(row['ranking_algorithm'], 'first_session_feed')

    def test_proposal_is_bounded_detached_value_data(self):
        proposal = self.proposal()
        self.assertEqual(len(proposal.finalists), 20)
        self.assertLessEqual(len(proposal.candidate_ids), 400)
        def detached(value):
            self.assertFalse(hasattr(value, '_sa_instance_state'))
            if is_dataclass(value):
                for field in fields(value):
                    detached(getattr(value, field.name))
            elif isinstance(value, tuple):
                for item in value:
                    detached(item)
            else:
                self.assertIsInstance(value, (str, int, float, bool, type(None)))
        detached(proposal)
        with self.assertRaises(FrozenInstanceError):
            proposal.revision += 1
        self.assertEqual(self.count(ProductImpression), 0)

    def test_profile_change_discards_old_page_and_reranks_once(self):
        # Warm-up may have liked the sole 300 RUB boundary SKU. Add independent
        # low-price positives after warm-up so the new budget has real winners.
        for index in range(5):
            self.fixture.product(f'cheap-after-swipe-{index}', price=Decimal('399'))
        self.db.commit()
        original = feeds._publish
        def publish(sessions, user_id, proposal, **options):
            if publishing.call_count == 1:
                with sessions() as db:
                    with profile_input_mutation(db, user_id):
                        db.scalar(select(FitProfile).where(FitProfile.user_id == user_id)).budget_max = 500
                    db.commit()
            return original(sessions, user_id, proposal, **options)
        with patch.object(feeds, '_publish', side_effect=publish) as publishing:
            result = feeds.build_feed_response(self.bind, self.user_id, limit=20)
        self.assertEqual(publishing.call_count, 2)
        self.assertTrue(result)
        self.assertTrue(all(Decimal(str(row['product']['price'])) <= 500 for row in result))
        self.assertEqual(self.count(ProductImpression), len(result))

    def test_changed_product_evidence_is_never_serialized_with_old_score(self):
        original = feeds._publish
        changed = []
        def publish(sessions, user_id, proposal, **options):
            if not changed:
                changed.append(proposal.finalists[0].product_id)
                with sessions() as db:
                    db.get(Product, changed[0]).price = Decimal('10000.99')
                    db.commit()
            return original(sessions, user_id, proposal, **options)
        with patch.object(feeds, '_publish', side_effect=publish) as publishing:
            result = feeds.build_feed_response(self.bind, self.user_id, limit=20)
        self.assertEqual(publishing.call_count, 2)
        self.assertEqual(len(result), 20)
        self.assertNotIn(changed[0], {row['product']['id'] for row in result})
        self.assertEqual(self.count(ProductImpression), 20)

    def test_new_source_rule_revalidates_finalists_and_reranks(self):
        with self.sessions() as db:
            db.add(ProductSource(id='feed-source', code='feed-test', name='Fixture', network='direct'))
            db.flush()
            for row in db.scalars(select(Product)):
                row.source_id = 'feed-source'
            db.commit()
        original = feeds._publish
        def publish(sessions, user_id, proposal, **options):
            if publishing.call_count == 1:
                with sessions() as db:
                    db.add(SourceRule(id='feed-blocked-brand', source_id='feed-source',
                        rule_type='blocked_brand', rule_value='One affordable brand', is_active=1))
                    db.commit()
            return original(sessions, user_id, proposal, **options)
        with patch.object(feeds, '_publish', side_effect=publish) as publishing:
            result = feeds.build_feed_response(self.bind, self.user_id, limit=20)
        self.assertEqual(publishing.call_count, 2)
        self.assertEqual(result, [])
        self.assertEqual(self.count(ProductImpression), 0)

    def test_stock_expiring_during_rank_is_rechecked_at_publication_time(self):
        original = feeds._publish
        with patch.object(stock_freshness, 'datetime', wraps=datetime) as clock:
            now = datetime.now(timezone.utc)
            clock.now.return_value = now
            def publish(sessions, user_id, proposal, **options):
                if publishing.call_count == 1:
                    self.assertTrue(proposal.finalists)
                    # No row/revision changes: only the stock observation ages.
                    clock.now.return_value = now + timedelta(hours=73)
                return original(sessions, user_id, proposal, **options)
            with patch.object(feeds, '_publish', side_effect=publish) as publishing:
                response = feeds.build_feed_response(self.bind, self.user_id, limit=20)
        self.assertEqual(response, [])
        self.assertEqual(publishing.call_count, 2)
        self.assertEqual(self.count(ProductImpression), 0)

    def test_two_conflicts_return_409_without_partial_allocation(self):
        original = feeds._publish
        def publish(sessions, user_id, proposal, **options):
            with sessions() as db:
                db.get(Product, proposal.finalists[0].product_id).price += Decimal('0.01')
                db.commit()
            return original(sessions, user_id, proposal, **options)
        with patch.object(feeds, '_publish', side_effect=publish) as publishing:
            with self.assertRaises(HTTPException) as error:
                feeds.build_feed_response(self.bind, self.user_id, limit=20)
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(publishing.call_count, 2)
        self.assertEqual(self.count(ProductImpression), 0)
        with self.sessions() as db:
            self.assertEqual(db.get(ActivationSession, self.user_id).issued_feed_ids, [])

    def test_imported_hidden_identity_cannot_reappear_as_unchanged_finalist(self):
        hidden = self.fixture.product('already-hidden-item')
        self.db.commit()
        record_product_event(self.db, self.fixture.user,
            EventRequest(product_id=hidden.id, event_type='dislike'))
        original = feeds._publish
        forbidden = []
        def publish(sessions, user_id, proposal, **options):
            if not forbidden:
                forbidden.append(proposal.finalists[0].product_id)
                with sessions() as db:
                    # Only the exclusion-side product changes; the finalist's
                    # entire evidence and the personal revision stay unchanged.
                    db.get(Product, hidden.id).image_url = db.get(Product, forbidden[0]).image_url
                    db.commit()
            return original(sessions, user_id, proposal, **options)
        with patch.object(feeds, '_publish', side_effect=publish) as publishing:
            response = feeds.build_feed_response(self.bind, self.user_id, limit=20)
        self.assertEqual(publishing.call_count, 2)
        self.assertEqual(len(response), 20)
        self.assertNotIn(forbidden[0], {row['product']['id'] for row in response})
        self.assertEqual(self.count(ProductImpression), 20)

    def test_null_gender_is_controlled_admission_failure(self):
        with self.sessions() as db:
            with profile_input_mutation(db, self.user_id):
                db.scalar(select(FitProfile).where(FitProfile.user_id == self.user_id)).gender_target = None
            db.commit()
        with patch.object(feeds, 'generate_feed') as ranking, self.assertRaises(HTTPException) as error:
            feeds.build_feed_response(self.bind, self.user_id)
        self.assertEqual(error.exception.status_code, 409)
        ranking.assert_not_called()
        self.assertEqual(self.count(ProductImpression), 0)

    def test_owner_removed_between_prepare_and_publish_requires_reauthentication(self):
        original = feeds._publish
        def publish(sessions, user_id, proposal, **options):
            with sessions() as db:
                # This isolated SQLite fixture has no FK cascades enabled. No
                # real account is removed; exercise the absent-owner boundary.
                db.execute(delete(User).where(User.id == user_id))
                db.commit()
            return original(sessions, user_id, proposal, **options)
        with patch.object(feeds, '_publish', side_effect=publish), self.assertRaises(HTTPException) as error:
            feeds.build_feed_response(self.bind, self.user_id, limit=20)
        self.assertEqual(error.exception.status_code, 401)
        self.assertEqual(self.count(ProductImpression), 0)

    def test_all_three_http_aliases_use_independent_pipeline(self):
        for module, function, extra in (
                (products_api, products_api.feed, dict(min_price=None, max_price=None,
                    categories=[], sizes=[], colors=[], exclude_ids=[])),
                (products_api, products_api.products_recommended, {}),
                (recommendations_api, recommendations_api.recommendations_feed, {})):
            with self.subTest(route=function.__name__), Session(self.bind) as auth_db:
                user = auth_db.get(User, self.user_id)
                with patch.object(module, 'user_from_token', return_value=user), patch.object(
                        feeds, 'build_feed_response', wraps=feeds.build_feed_response) as pipeline:
                    response = function(limit=20, source=None, scenario='daily', db=auth_db,
                        credentials=None, **extra)
                    self.assertEqual(len(response), 20)
                    pipeline.assert_called_once()
                    self.assertFalse(auth_db.in_transaction())

    def test_explicit_request_exclusions_and_category_filter_are_preserved(self):
        result = feeds.build_feed_response(self.bind, self.user_id, limit=20,
            filters=CatalogFilters(categories=('tops',)))
        self.assertTrue(result)
        excluded = [row['product']['id'] for row in result]
        rest = feeds.build_feed_response(self.bind, self.user_id, limit=20,
            filters=CatalogFilters(categories=('tops',)), exclude_ids=excluded)
        self.assertTrue(set(excluded).isdisjoint(row['product']['id'] for row in rest))

    def test_warm_legacy_account_keeps_ranking_contract_and_bounded_caches(self):
        with self.sessions() as db:
            db.delete(db.get(ActivationSession, self.user_id))
            # This case compares legacy ranking/caching on positive SKU. The
            # negative oracle above is for activation's narrower segment (it
            # rejects sports/unisex, which legacy explicitly permits).
            db.execute(delete(Product).where(Product.id.in_(self.fixture.bad)))
            db.commit()
        as_of = datetime.now(timezone.utc)
        with self.sessions() as db:
            db.info['recommendation_as_of'] = as_of
            previous = products_api._feed_scored_items(db, db.get(User, self.user_id), limit=20)
        with patch.object(feeds, 'datetime', wraps=datetime) as clock:
            clock.now.return_value = as_of
            response = feeds.build_feed_response(self.bind, self.user_id, limit=20)
        self.assertTrue(response)
        self.assertEqual(response, previous)
        self.fixture.assert_oracle(row['product']['id'] for row in response)
        with self.sessions() as db:
            pool = db.scalar(select(RecommendationCandidatePool).where(
                RecommendationCandidatePool.user_id == self.user_id))
            cache = db.scalar(select(UserRecommendationCache).where(UserRecommendationCache.user_id == self.user_id))
            self.assertLessEqual(len(pool.product_ids), 400)
            self.assertEqual(cache.top_product_ids, [row['product']['id'] for row in response])
