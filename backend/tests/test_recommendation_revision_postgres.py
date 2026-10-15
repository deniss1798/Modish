"""Opt-in tests against the disposable workspace PostgreSQL cluster only.

Set MODISH_ISOLATED_PG_RUNTIME to the locally generated runtime metadata.
This suite never reads the application's DATABASE_URL or an existing service's
credentials. It checks the server data directory before any schema mutation.
These are revision prerequisites, not proof that ranking has been split yet.
"""
from concurrent.futures import ThreadPoolExecutor
import os
import threading
import unittest

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import StyleProfile, User
from app.services.recommendation_revision import owner_mutation
from test_recommendation_revision_migration import migration
from isolated_postgres import create_isolated_postgres


@unittest.skipUnless(os.getenv('MODISH_ISOLATED_PG_RUNTIME'), 'Opt-in disposable PostgreSQL fixture')
class RecommendationRevisionPostgresTests(unittest.TestCase):
    def setUp(self):
        fixture = create_isolated_postgres(self, namespace='revision')
        self.admin, self.engine, self.schema = fixture.admin, fixture.engine, fixture.schema
        with Session(self.engine) as db:
            db.add(User(id='owner', email='pg-revision@example.test', password_hash='unused'))
            db.add(StyleProfile(id='style', user_id='owner', style_target='unknown'))
            db.commit()

    def test_two_connections_serialize_updates_and_reload_previously_cached_inputs(self):
        loaded, owner_locked, attempting, acquired, release = (threading.Event() for _ in range(5))

        def first():
            with Session(self.engine, autoflush=False) as db:
                with owner_mutation(db, 'owner') as change:
                    change.owner.height_cm = 180
                    db.get(StyleProfile, 'style').style_target = 'womenswear'
                    change.mark_changed()
                    owner_locked.set()
                    if not release.wait(4):
                        raise AssertionError('Missing deterministic release')
                db.commit()

        def second():
            with Session(self.engine, autoflush=False) as db:
                old_user = db.get(User, 'owner')
                old_style = db.get(StyleProfile, 'style')
                loaded.set()
                if not owner_locked.wait(4):
                    raise AssertionError('First writer did not lock')
                attempting.set()
                with owner_mutation(db, 'owner') as change:
                    acquired.set()
                    self.assertEqual(old_user.height_cm, 180)
                    self.assertEqual(old_style.style_target, 'womenswear')
                    change.owner.height_cm += 1
                    change.mark_changed()
                db.commit()

        with ThreadPoolExecutor(max_workers=2) as executor:
            later = executor.submit(second)
            self.assertTrue(loaded.wait(4))
            earlier = executor.submit(first)
            try:
                self.assertTrue(attempting.wait(4))
                self.assertFalse(acquired.wait(.1), 'Second writer bypassed PostgreSQL owner row lock')
            finally:
                release.set()
            earlier.result(timeout=5)
            later.result(timeout=5)
        with Session(self.engine) as db:
            owner = db.get(User, 'owner')
            self.assertEqual(owner.height_cm, 181)
            self.assertEqual(owner.recommendation_revision, 2)

    def test_postgres_rollback_and_retry_preserve_monotonic_revision(self):
        with Session(self.engine) as db:
            with owner_mutation(db, 'owner') as change:
                change.owner.height_cm = 190
                change.mark_changed()
            db.rollback()
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(User.recommendation_revision)), 0)
            self.assertEqual(db.get(User, 'owner').height_cm, 170)
            with owner_mutation(db, 'owner') as change:
                change.owner.height_cm = 175
                change.mark_changed()
            db.commit()
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(User.recommendation_revision)), 1)
            self.assertEqual(db.get(User, 'owner').height_cm, 175)

    def test_actual_postgres_migration_preserves_owner_and_installs_default(self):
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        with self.engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
                migration.upgrade()
            self.assertEqual(connection.execute(text('SELECT id, height_cm, recommendation_revision FROM users')).one(),
                ('owner', 170, 0))
