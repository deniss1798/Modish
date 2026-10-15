"""Read-only deck preparation and atomic, current-evidence publication."""
from dataclasses import FrozenInstanceError
from decimal import Decimal
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import event, select
from sqlalchemy.orm import sessionmaker

from app.api import onboarding
from app.models import ActivationSession, Product, RecommendationEventV2, User
from app.services import activation_deck_session as decks, activation_service as activation
from app.services.recommendation_revision import owner_mutation
import test_closed_alpha_first_session as fixtures


class ActivationDeckSessionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ClosedAlphaFirstSessionTests()
        scenario = self.fixture.scenario()
        scenario.__enter__()
        self.addCleanup(scenario.__exit__, None, None, None)
        self.fixture.positive_catalogue()
        self.fixture.negative_catalogue()
        self.db = self.fixture.db
        self.bind = self.db.get_bind()
        self.user_id = self.fixture.user.id
        self.db.commit()
        self.sessions = sessionmaker(bind=self.bind, autoflush=False, expire_on_commit=False)

    def test_cold_swipe_rank_is_readonly_and_delivery_does_not_train_taste(self):
        statements = []
        original = decks.swipe_deck
        def rank(db, user, activation, **options):
            self.assertTrue(options['read_only'])
            def record(_connection, _cursor, statement, *_args):
                statements.append(statement.strip().split()[0].upper())
            event.listen(self.bind, 'before_cursor_execute', record)
            try:
                return original(db, user, activation, **options)
            finally:
                event.remove(self.bind, 'before_cursor_execute', record)
        with self.sessions() as db:
            revision = db.get(User, self.user_id).recommendation_revision
        with patch.object(decks, 'swipe_deck', side_effect=rank):
            response = decks.build_swipe_deck(self.bind, self.user_id)
        ids = [row['id'] for row in response['products']]
        self.assertEqual(len(ids), 20)
        self.fixture.assert_oracle(ids)
        self.assertEqual(set(statements), {'SELECT'})
        self.assertEqual({key: value for key, value in response.items() if key != 'products'},
            {'rated': 0, 'minimum': 15, 'target': 20, 'can_continue': False})
        with self.sessions() as db:
            self.assertEqual(db.get(User, self.user_id).recommendation_revision, revision)
            self.assertEqual(db.get(ActivationSession, self.user_id).swipe_product_ids, ids)
            self.assertEqual(list(db.scalars(select(RecommendationEventV2))), [])
        proposal = decks._prepare(self.sessions, self.user_id)
        self.assertIsInstance(proposal.allocation, tuple)
        with self.assertRaises(FrozenInstanceError):
            proposal.revision += 1

    def test_feedback_between_phases_is_counted_and_old_card_is_not_reissued(self):
        first = decks.build_swipe_deck(self.bind, self.user_id)
        liked = first['products'][0]['id']
        original = decks._publish
        def publish(sessions, user_id, proposal):
            if publishing.call_count == 1:
                with sessions() as db:
                    activation.swipe_feedback(db, db.get(User, user_id),
                        db.get(ActivationSession, user_id), liked, 'like')
                    db.commit()
            return original(sessions, user_id, proposal)
        with patch.object(decks, '_publish', side_effect=publish) as publishing:
            response = decks.build_swipe_deck(self.bind, self.user_id)
        self.assertEqual(publishing.call_count, 2)
        self.assertEqual(response['rated'], 1)
        self.assertEqual(len(response['products']), 19)
        self.assertNotIn(liked, {row['id'] for row in response['products']})
        self.fixture.assert_oracle(row['id'] for row in response['products'])

    def test_catalogue_change_reranks_without_allocating_stale_first_page(self):
        original = decks._publish
        forbidden = []
        def publish(sessions, user_id, proposal):
            if not forbidden:
                forbidden.append(proposal.finalists[0].product_id)
                with sessions() as db:
                    db.get(Product, forbidden[0]).available_sizes = ['XL']
                    db.commit()
            return original(sessions, user_id, proposal)
        with patch.object(decks, '_publish', side_effect=publish) as publishing:
            response = decks.build_swipe_deck(self.bind, self.user_id)
        self.assertEqual(publishing.call_count, 2)
        self.assertEqual(len(response['products']), 20)
        self.assertNotIn(forbidden[0], {row['id'] for row in response['products']})
        with self.sessions() as db:
            self.assertNotIn(forbidden[0], db.get(ActivationSession, self.user_id).swipe_product_ids)

    def test_concurrent_deck_allocation_is_not_overwritten(self):
        first = decks._prepare(self.sessions, self.user_id)
        allocated_elsewhere = first.finalists[0].product_id
        original = decks._publish
        def publish(sessions, user_id, proposal):
            if publishing.call_count == 1:
                with sessions() as db:
                    with owner_mutation(db, user_id):
                        db.get(ActivationSession, user_id).swipe_product_ids = [allocated_elsewhere]
                    db.commit()
            return original(sessions, user_id, proposal)
        with patch.object(decks, '_publish', side_effect=publish) as publishing:
            response = decks.build_swipe_deck(self.bind, self.user_id)
        self.assertEqual(publishing.call_count, 2)
        self.assertEqual(len(response['products']), 20)
        self.assertEqual(response['products'][0]['id'], allocated_elsewhere)
        with self.sessions() as db:
            self.assertEqual(db.get(ActivationSession, self.user_id).swipe_product_ids[0], allocated_elsewhere)

    def test_persistent_conflict_has_no_partial_deck(self):
        original = decks._publish
        def publish(sessions, user_id, proposal):
            with sessions() as db:
                db.get(Product, proposal.finalists[0].product_id).price += Decimal('0.01')
                db.commit()
            return original(sessions, user_id, proposal)
        with patch.object(decks, '_publish', side_effect=publish) as publishing, self.assertRaises(HTTPException) as error:
            decks.build_swipe_deck(self.bind, self.user_id)
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(publishing.call_count, 2)
        with self.sessions() as db:
            self.assertEqual(db.get(ActivationSession, self.user_id).swipe_product_ids, [])
            self.assertEqual(list(db.scalars(select(RecommendationEventV2))), [])

    def test_cached_imported_alias_of_rated_card_is_removed_and_refilled(self):
        first = decks.build_swipe_deck(self.bind, self.user_id)
        liked, alias = first['products'][0]['id'], first['products'][1]['id']
        with self.sessions() as db:
            activation.swipe_feedback(db, db.get(User, self.user_id),
                db.get(ActivationSession, self.user_id), liked, 'like')
            db.get(Product, alias).image_url = db.get(Product, liked).image_url
            db.commit()
        response = decks.build_swipe_deck(self.bind, self.user_id)
        self.assertEqual(len(response['products']), 19)
        self.assertTrue({liked, alias}.isdisjoint(row['id'] for row in response['products']))

    def test_http_entry_closes_auth_read_before_starting_independent_sessions(self):
        user = self.db.get(User, self.user_id)
        with patch.object(onboarding, 'user_from_token', return_value=user), patch.object(
                decks, 'build_swipe_deck', wraps=decks.build_swipe_deck) as pipeline:
            response = onboarding.get_style_swipe(db=self.db, credentials=None)
        self.assertEqual(len(response['products']), 20)
        pipeline.assert_called_once()
        self.assertFalse(self.db.in_transaction())
