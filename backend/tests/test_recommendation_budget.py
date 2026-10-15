"""Deadline failures abort computation rather than publishing a partial page."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from app.models import ActivationSession, ProductImpression
from app.services import recommendation_budget as budget, feed_session, activation_deck_session, intent_session
import test_feed_session as feed_fixtures
import test_activation_deck_session as deck_fixtures
import test_intent_session as intent_fixtures


class RecommendationBudgetTests(unittest.TestCase):
    def test_deadline_is_monotonic_and_absent_budget_has_no_global_clock_work(self):
        db = SimpleNamespace(info={}, get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name='sqlite')))
        with patch.object(budget, 'monotonic') as clock:
            budget.check_ranking_budget(db)
            clock.assert_not_called()
            clock.return_value = 100
            budget.begin_ranking(db)
            clock.return_value = 114.999
            budget.check_ranking_budget(db)
            clock.return_value = 115
            with self.assertRaises(budget.RecommendationRankingTimeout):
                budget.check_ranking_budget(db)
        other = SimpleNamespace(info={})
        budget.check_ranking_budget(other)

    def test_sql_timeout_tracks_remaining_seconds_without_a_roundtrip_per_item(self):
        db = SimpleNamespace(info={}, execute=Mock(),
            get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name='postgresql')))
        with patch.object(budget, 'monotonic') as clock:
            clock.side_effect = [100, 100.1]
            budget.begin_ranking(db)
            clock.side_effect = None
            clock.return_value = 100.2
            for _ in range(100):
                budget.check_ranking_budget(db)
            self.assertEqual(db.execute.call_count, 1)
            clock.return_value = 101.2
            budget.check_ranking_budget(db)
        self.assertEqual(db.execute.call_count, 2)
        statements = [str(call.args[0]) for call in db.execute.call_args_list]
        self.assertTrue(all(sql.startswith('SET LOCAL statement_timeout = ') for sql in statements))
        durations = [int(sql.split("'")[1].removesuffix('ms')) for sql in statements]
        self.assertGreater(durations[0], durations[1])
        self.assertGreater(durations[1], 0)

    def test_nonpositive_budget_is_rejected(self):
        for seconds in (0, -1):
            with self.assertRaises(ValueError):
                budget.begin_ranking(SimpleNamespace(info={}), seconds=seconds)


class RecommendationBudgetFlowTests(unittest.TestCase):
    def fixture(self, kind):
        instance = kind()
        instance.setUp()
        self.addCleanup(instance.doCleanups)
        return instance

    def assert_timeout(self, operation):
        with self.assertRaises(HTTPException) as error:
            operation()
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(error.exception.detail, budget.TIMEOUT_MESSAGE)

    def test_feed_deadline_after_rank_does_not_retry_or_write_impressions(self):
        f = self.fixture(feed_fixtures.FeedSessionTests)
        original = feed_session.generate_feed
        with patch.object(budget, 'monotonic', return_value=0) as clock:
            def rank(*args, **kwargs):
                result = original(*args, **kwargs)
                clock.return_value = 16
                return result
            with patch.object(feed_session, 'generate_feed', side_effect=rank) as ranking:
                self.assert_timeout(lambda: feed_session.build_feed_response(f.bind, f.user_id))
        self.assertEqual(ranking.call_count, 1)
        self.assertEqual(f.count(ProductImpression), 0)

    def test_deck_deadline_does_not_publish_allocation(self):
        f = self.fixture(deck_fixtures.ActivationDeckSessionTests)
        original = activation_deck_session.swipe_deck
        with patch.object(budget, 'monotonic', return_value=0) as clock:
            def rank(*args, **kwargs):
                result = original(*args, **kwargs)
                clock.return_value = 16
                return result
            with patch.object(activation_deck_session, 'swipe_deck', side_effect=rank) as ranking:
                self.assert_timeout(lambda: activation_deck_session.build_swipe_deck(f.bind, f.user_id))
        self.assertEqual(ranking.call_count, 1)
        with f.sessions() as db:
            self.assertEqual(db.get(ActivationSession, f.user_id).swipe_product_ids, [])

    def test_intent_deadline_releases_claim_and_same_key_recovers_once(self):
        f = self.fixture(intent_fixtures.IntentSessionTests)
        original = intent_session.item_results
        with patch.object(budget, 'monotonic', return_value=0) as clock:
            def rank(*args, **kwargs):
                result = original(*args, **kwargs)
                clock.return_value = 16
                return result
            with patch.object(intent_session, 'item_results', side_effect=rank) as ranking:
                self.assert_timeout(f.build)
        self.assertEqual(ranking.call_count, 1)
        self.assertEqual(f.metrics(), 0)
        self.assertNotIn('result_version', f.intent().context_json)
        f.assert_oracle(f.build())
        self.assertEqual(f.metrics(), 1)

    def test_sql_deadline_in_prepare_is_not_retried_as_a_catalogue_conflict(self):
        class QueryTimeout(Exception):
            sqlstate = '57014'
        f = self.fixture(intent_fixtures.IntentSessionTests)
        failure = DBAPIError('SELECT synthetic_fixture', {}, QueryTimeout())
        with patch.object(intent_session, '_prepare', side_effect=failure) as prepare:
            self.assert_timeout(f.build)
        self.assertEqual(prepare.call_count, 1)
        self.assertEqual(f.metrics(), 0)
