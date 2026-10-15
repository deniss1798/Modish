import importlib.util
import io
from pathlib import Path
import unittest

from alembic.migration import MigrationContext
from alembic.operations import Operations
import sqlalchemy as sa

path = Path(__file__).resolve().parents[1] / 'alembic/versions/20261015_0028_recommendation_revision.py'
spec = importlib.util.spec_from_file_location('recommendation_revision_migration', path)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class RecommendationRevisionMigrationTests(unittest.TestCase):
    def test_upgrade_default_and_downgrade_preserve_existing_owner_data(self):
        engine = sa.create_engine('sqlite://')
        self.addCleanup(engine.dispose)
        with engine.begin() as connection:
            connection.execute(sa.text('CREATE TABLE users (id VARCHAR PRIMARY KEY, height_cm INTEGER)'))
            connection.execute(sa.text("INSERT INTO users (id, height_cm) VALUES ('retained', 175)"))
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
            self.assertEqual(connection.execute(sa.text('SELECT id, height_cm, recommendation_revision FROM users')).one(),
                ('retained', 175, 0))
            connection.execute(sa.text("UPDATE users SET recommendation_revision=recommendation_revision+1 WHERE id='retained'"))
            self.assertEqual(connection.scalar(sa.text('SELECT recommendation_revision FROM users')), 1)
            column = next(item for item in sa.inspect(connection).get_columns('users') if item['name'] == 'recommendation_revision')
            self.assertFalse(column['nullable'])
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
            self.assertEqual(connection.execute(sa.text('SELECT id, height_cm FROM users')).one(), ('retained', 175))
            self.assertNotIn('recommendation_revision', {item['name'] for item in sa.inspect(connection).get_columns('users')})

    def test_postgres_sql_and_additive_chain(self):
        output = io.StringIO()
        context = MigrationContext.configure(dialect_name='postgresql', opts={'as_sql': True, 'output_buffer': output})
        with Operations.context(context):
            migration.upgrade()
        self.assertIn('ADD COLUMN recommendation_revision INTEGER DEFAULT', output.getvalue())
        self.assertIn('NOT NULL', output.getvalue())
        self.assertNotIn('UPDATE ', output.getvalue())
        self.assertEqual(migration.down_revision, '20261015_0027')
