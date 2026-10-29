"""Actual Alembic28->29 checks, only in the verified disposable local PG."""
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.db import Base
from app.models import User
from app.services.catalog.refresh_ledger import refresh_runs
from isolated_postgres import create_isolated_postgres
from test_refresh_migration import ledger_diffs, migration, progress_values


@unittest.skipUnless(os.getenv('MODISH_ISOLATED_PG_RUNTIME'), 'Opt-in disposable PostgreSQL fixture')
class RefreshMigrationPostgresTests(unittest.TestCase):
    def setUp(self):
        self.fixture = create_isolated_postgres(self, namespace='refresh17_schema')
        self.engine = self.fixture.engine

    def migrate(self, direction, revision):
        # The fixture already verified the server data directory and generated
        # this schema. Never inherit DATABASE_URL, .env or a public search path.
        backend = Path(__file__).resolve().parents[1]
        config = Config(str(backend / 'alembic.ini'))
        config.set_main_option('script_location', str(backend / 'alembic'))
        config.set_main_option('prepend_sys_path', str(backend))
        environment = {key: value for key, value in os.environ.items()
            if key.upper() in {'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'PATH', 'PATHEXT', 'COMSPEC'}}
        environment.update(DATABASE_URL=self.engine.url.set(query={}).render_as_string(hide_password=False),
            PGOPTIONS=f'-c search_path={self.fixture.schema} -c statement_timeout=10000 -c lock_timeout=2000',
            APP_ENV='test', PYTHON_DOTENV_DISABLED='1', JWT_SECRET='SYNTHETIC_REFRESH29_MIGRATION')
        with patch.dict(os.environ, environment, clear=True), patch('dotenv.load_dotenv', return_value=False):
            getattr(command, direction)(config, revision)

    def test_actual_alembic_chain_is_additive_and_autogenerate_matches_ledger(self):
        Base.metadata.drop_all(self.engine)
        self.migrate('upgrade', '20261015_0028')
        with Session(self.engine) as db:
            db.add(User(id='retained-owner', email='refresh29@example.test', password_hash='authored-unused'))
            db.commit()
        with self.engine.connect() as connection:
            before = dict(connection.execute(sa.text('SELECT * FROM users')).mappings().one())
            old_tables = set(sa.inspect(connection).get_table_names())
            self.assertNotIn('catalog_refresh_runs', old_tables)
        self.migrate('upgrade', '20261028_0029')
        with self.engine.connect() as connection:
            self.assertEqual(connection.scalar(sa.text('SELECT version_num FROM alembic_version')),
                '20261028_0029')
            self.assertEqual(set(sa.inspect(connection).get_table_names()), old_tables | {'catalog_refresh_runs'})
            self.assertEqual(dict(connection.execute(sa.text('SELECT * FROM users')).mappings().one()), before)
            self.assertEqual(ledger_diffs(connection), [])
        self.migrate('downgrade', '20261015_0028')
        with self.engine.connect() as connection:
            self.assertEqual(connection.scalar(sa.text('SELECT version_num FROM alembic_version')),
                '20261015_0028')
            self.assertEqual(set(sa.inspect(connection).get_table_names()), old_tables)
            self.assertEqual(dict(connection.execute(sa.text('SELECT * FROM users')).mappings().one()), before)

    def test_unfinished_ledger_prevents_drop_and_rollback_preserves_rows(self):
        with self.engine.begin() as connection:
            connection.execute(sa.insert(refresh_runs), progress_values(status='interrupted'))
        with self.assertRaisesRegex(DBAPIError, 'unfinished_catalogue_refresh'):
            with self.engine.begin() as connection:
                with Operations.context(MigrationContext.configure(connection)):
                    migration.downgrade()
        with self.engine.connect() as connection:
            self.assertEqual(connection.scalar(sa.select(refresh_runs.c.status)), 'interrupted')
            self.assertEqual(ledger_diffs(connection), [])

    def test_worker_session_advisory_owner_prevents_even_empty_ledger_drop(self):
        with self.engine.connect() as owner:
            self.assertTrue(owner.scalar(sa.text('SELECT pg_try_advisory_lock(20261028, 17)')))
            owner.commit()
            try:
                with self.assertRaisesRegex(DBAPIError, 'worker_must_be_stopped_before_downgrade'):
                    with self.engine.begin() as connection:
                        with Operations.context(MigrationContext.configure(connection)):
                            migration.downgrade()
                with self.engine.connect() as connection:
                    self.assertEqual(ledger_diffs(connection), [])
            finally:
                owner.execute(sa.text('SELECT pg_advisory_unlock(20261028, 17)'))
                owner.commit()


if __name__ == '__main__':
    unittest.main()
