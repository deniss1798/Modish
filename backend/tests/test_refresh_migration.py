"""One model/migration definition for the next release's durable progress."""
from datetime import datetime, timezone
import importlib.util
import io
from pathlib import Path
import unittest

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
import sqlalchemy as sa

from app.db import Base
from app.models import CatalogRefreshRun
from app.services.catalog.refresh_ledger import refresh_runs


path = Path(__file__).resolve().parents[1] / 'alembic/versions/20261028_0029_catalog_refresh_runs.py'
spec = importlib.util.spec_from_file_location('refresh29_migration', path)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


def include_ledger(obj, name, kind, reflected, compare_to):
    if kind == 'table':
        return name == 'catalog_refresh_runs'
    return getattr(getattr(obj, 'table', None), 'name', None) == 'catalog_refresh_runs'


def ledger_diffs(connection):
    context = MigrationContext.configure(connection, opts={'include_object': include_ledger,
        'compare_type': True, 'compare_server_default': True})
    return compare_metadata(context, Base.metadata)


def progress_values(*, status='running'):
    now = datetime.now(timezone.utc)
    return dict(id='authored-refresh-run', snapshot_sha256='a' * 64, operation_count=1,
        cursor=0, finalized_sources=0, last_batch_sha256='', status=status,
        counts_json={}, source_states_json={}, started_at=now, updated_at=now)


class RefreshMigrationTests(unittest.TestCase):
    def setUp(self):
        self.engine = sa.create_engine('sqlite://')
        self.addCleanup(self.engine.dispose)

    def test_shared_model_metadata_detects_missing_table_and_matches_real_upgrade(self):
        self.assertIs(refresh_runs, CatalogRefreshRun.__table__)
        self.assertIs(refresh_runs.metadata, Base.metadata)
        self.assertIs(Base.metadata.tables['catalog_refresh_runs'], refresh_runs)
        with self.engine.begin() as connection:
            before = ledger_diffs(connection)
            self.assertEqual([(change[0], change[1].name) for change in before],
                [('add_table', 'catalog_refresh_runs')])
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
            self.assertEqual(ledger_diffs(connection), [])
            self.assertEqual(migration.down_revision, '20261015_0028')
            self.assertEqual(migration.revision, '20261028_0029')

    def test_downgrade_refuses_unfinished_progress_and_preserves_other_data(self):
        with self.engine.begin() as connection:
            connection.execute(sa.text('CREATE TABLE retained(id INTEGER PRIMARY KEY, value TEXT)'))
            connection.execute(sa.text("INSERT INTO retained VALUES(1, 'authored value')"))
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
            for status in ('running', 'interrupted', 'unknown'):
                with self.subTest(status=status):
                    connection.execute(sa.insert(refresh_runs), progress_values(status=status))
                    with Operations.context(MigrationContext.configure(connection)):
                        with self.assertRaisesRegex(RuntimeError, 'unfinished_catalogue_refresh'):
                            migration.downgrade()
                    self.assertEqual(connection.scalar(sa.select(refresh_runs.c.status)), status)
                    connection.execute(sa.delete(refresh_runs))
            connection.execute(sa.insert(refresh_runs), progress_values(status='complete'))
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
            self.assertNotIn('catalog_refresh_runs', sa.inspect(connection).get_table_names())
            self.assertEqual(connection.execute(sa.text('SELECT * FROM retained')).one(),
                (1, 'authored value'))

    def test_offline_postgres_upgrade_and_downgrade_keep_execution_time_guards(self):
        output = io.StringIO()
        context = MigrationContext.configure(dialect_name='postgresql',
            opts={'as_sql': True, 'output_buffer': output})
        with Operations.context(context):
            migration.upgrade()
            migration.downgrade()
        sql = output.getvalue()
        self.assertIn('CREATE TABLE catalog_refresh_runs', sql)
        self.assertIn('snapshot_sha256 VARCHAR(64) NOT NULL', sql)
        self.assertIn('pg_try_advisory_xact_lock(20261028, 17)', sql)
        self.assertIn('ACCESS EXCLUSIVE MODE NOWAIT', sql)
        self.assertIn("WHERE status <> 'complete'", sql)
        self.assertLess(sql.index('unfinished_catalogue_refresh_prevents_downgrade'),
            sql.index('DROP TABLE catalog_refresh_runs'))


if __name__ == '__main__':
    unittest.main()
