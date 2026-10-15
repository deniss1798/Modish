"""Mutation revision lifetime; SQLite checks accounting, not row-lock waits."""
import unittest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db import Base
from app.models import StyleProfile, User
from app.services.recommendation_revision import owner_mutation


class RecommendationRevisionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine, autoflush=False)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        self.user = User(id='revision-owner', email='revision@example.test', password_hash='unused')
        self.db.add(self.user)
        self.db.commit()

    def revision(self):
        return self.db.scalar(select(User.recommendation_revision).where(User.id == self.user.id))

    def test_new_owner_and_noop_keep_zero(self):
        self.assertEqual(self.revision(), 0)
        with owner_mutation(self.db, self.user.id):
            pass
        self.db.commit()
        self.assertEqual(self.revision(), 0)

    def test_nested_helpers_coalesce_once_and_preserve_outer_pending_changes(self):
        profile = StyleProfile(id='style', user_id=self.user.id, style_target='unknown')
        self.db.add(profile)
        self.db.commit()
        with owner_mutation(self.db, self.user.id) as outer:
            profile.style_target = 'womenswear'
            outer.mark_changed()
            with owner_mutation(self.db, self.user.id) as inner:
                self.assertIs(inner, outer)
                self.assertEqual(profile.style_target, 'womenswear')
                inner.mark_changed()
        with owner_mutation(self.db, self.user.id) as again:
            again.mark_changed()
        self.db.commit()
        self.assertEqual(self.revision(), 1)
        self.assertEqual(profile.style_target, 'womenswear')

    def test_rollback_rolls_back_data_revision_and_coalescing_state(self):
        with owner_mutation(self.db, self.user.id) as change:
            change.owner.height_cm = 180
            change.mark_changed()
        self.assertEqual(self.revision(), 1)
        self.db.rollback()
        self.assertEqual(self.revision(), 0)
        self.assertEqual(self.db.get(User, self.user.id).height_cm, 170)
        with owner_mutation(self.db, self.user.id) as retry:
            retry.owner.height_cm = 175
            retry.mark_changed()
        self.db.commit()
        self.assertEqual(self.revision(), 1)
        self.assertEqual(self.user.height_cm, 175)

    def test_savepoint_rollback_cannot_suppress_later_outer_change(self):
        with owner_mutation(self.db, self.user.id) as outer:
            savepoint = self.db.begin_nested()
            with owner_mutation(self.db, self.user.id) as nested:
                nested.owner.height_cm = 181
                nested.mark_changed()
            savepoint.rollback()
            self.assertEqual(self.revision(), 0)
            outer.owner.height_cm = 177
            outer.mark_changed()
        self.db.commit()
        self.assertEqual(self.revision(), 1)
        self.assertEqual(self.user.height_cm, 177)

    def test_rejected_mutation_does_not_mark_successful_revision(self):
        with self.assertRaisesRegex(ValueError, 'rejected'):
            with owner_mutation(self.db, self.user.id) as change:
                change.mark_changed()
                raise ValueError('rejected')
        self.assertEqual(self.revision(), 0)
        self.db.rollback()
        self.assertNotIn('_recommendation_owner_mutations', self.db.info)

    def test_independent_transactions_increment_monotonically(self):
        for expected in range(1, 4):
            with owner_mutation(self.db, self.user.id) as change:
                change.owner.height_cm = 170 + expected
                change.mark_changed()
            self.db.commit()
            self.assertEqual(self.revision(), expected)
