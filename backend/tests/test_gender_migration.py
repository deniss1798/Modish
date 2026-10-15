import importlib.util
import io
from pathlib import Path
import unittest

from alembic.migration import MigrationContext
from alembic.operations import Operations
import sqlalchemy as sa


_PATH = Path(__file__).resolve().parents[1] / 'alembic/versions/20261015_0027_nullable_user_gender.py'
_SPEC = importlib.util.spec_from_file_location('nullable_user_gender_migration', _PATH)
migration = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(migration)


class UserGenderMigrationTests(unittest.TestCase):
    def setUp(self):
        self.engine = sa.create_engine('sqlite://')
        metadata = sa.MetaData()
        users = sa.Table('users', metadata, sa.Column('id', sa.String, primary_key=True))
        fits = sa.Table('fit_profiles', metadata, sa.Column('id', sa.String, primary_key=True),
            sa.Column('user_id', sa.String, sa.ForeignKey('users.id'), unique=True, index=True),
            sa.Column('gender_target', sa.String(32), nullable=False, server_default='unisex'),
            sa.Column('size_profile_json', sa.JSON))
        links = sa.Table('saved_fit_links', metadata, sa.Column('id', sa.String, primary_key=True),
            sa.Column('fit_id', sa.String, sa.ForeignKey('fit_profiles.id')))
        metadata.create_all(self.engine)
        with self.engine.begin() as connection:
            connection.execute(users.insert(), [{'id': choice} for choice in ('menswear', 'womenswear', 'unisex', 'new')])
            connection.execute(fits.insert(), [{'id': choice, 'user_id': choice,
                'gender_target': choice, 'size_profile_json': {'height_confirmed': True}}
                for choice in ('menswear', 'womenswear', 'unisex')])
            connection.execute(links.insert(), {'id': 'retained-link', 'fit_id': 'womenswear'})

    def tearDown(self):
        self.engine.dispose()

    def run_migration(self, connection, direction):
        with Operations.context(MigrationContext.configure(connection)):
            getattr(migration, direction)()

    def assert_preserved(self, connection):
        self.assertEqual(connection.execute(sa.text("SELECT id, gender_target FROM fit_profiles WHERE id <> 'new' ORDER BY id")).all(),
            [('menswear', 'menswear'), ('unisex', 'unisex'), ('womenswear', 'womenswear')])
        self.assertEqual(connection.scalar(sa.text('SELECT fit_id FROM saved_fit_links')), 'womenswear')
        self.assertEqual(connection.exec_driver_sql('PRAGMA foreign_key_check').all(), [])
        self.assertTrue(any(index['column_names'] == ['user_id'] for index in sa.inspect(connection).get_indexes('fit_profiles')))

    def test_upgrade_preserves_choices_links_sizes_and_removes_implicit_default(self):
        with self.engine.begin() as connection:
            self.run_migration(connection, 'upgrade')
            column = next(item for item in sa.inspect(connection).get_columns('fit_profiles') if item['name'] == 'gender_target')
            self.assertTrue(column['nullable'])
            self.assertIsNone(column['default'])
            connection.execute(sa.text("INSERT INTO fit_profiles (id, user_id) VALUES ('new', 'new')"))
            self.assertIsNone(connection.scalar(sa.text("SELECT gender_target FROM fit_profiles WHERE id='new'")))
            self.assertEqual(connection.scalar(sa.text("SELECT size_profile_json FROM fit_profiles WHERE id='womenswear'")), '{"height_confirmed": true}')
            self.assert_preserved(connection)

    def test_downgrade_with_null_refuses_without_inferring_or_partially_changing_schema(self):
        with self.engine.begin() as connection:
            self.run_migration(connection, 'upgrade')
            connection.execute(sa.text("INSERT INTO fit_profiles (id, user_id) VALUES ('new', 'new')"))
            with self.assertRaisesRegex(RuntimeError, 'unset user gender'):
                self.run_migration(connection, 'downgrade')
            column = next(item for item in sa.inspect(connection).get_columns('fit_profiles') if item['name'] == 'gender_target')
            self.assertTrue(column['nullable'])
            self.assertIsNone(column['default'])
            self.assertIsNone(connection.scalar(sa.text("SELECT gender_target FROM fit_profiles WHERE id='new'")))
            self.assert_preserved(connection)

    def test_lossless_downgrade_when_every_user_has_an_explicit_choice(self):
        with self.engine.begin() as connection:
            self.run_migration(connection, 'upgrade')
            self.run_migration(connection, 'downgrade')
            column = next(item for item in sa.inspect(connection).get_columns('fit_profiles') if item['name'] == 'gender_target')
            self.assertFalse(column['nullable'])
            self.assertEqual(column['default'], "'unisex'")
            self.assert_preserved(connection)
            self.run_migration(connection, 'upgrade')
            self.assert_preserved(connection)

    def test_sqlite_foreign_key_enforcement_refuses_before_recreating_table(self):
        with self.engine.connect() as connection:
            connection.exec_driver_sql('PRAGMA foreign_keys=ON')
            connection.commit()
            with self.assertRaisesRegex(RuntimeError, 'foreign_keys=OFF'):
                self.run_migration(connection, 'upgrade')
            column = next(item for item in sa.inspect(connection).get_columns('fit_profiles') if item['name'] == 'gender_target')
            self.assertFalse(column['nullable'])
            self.assertEqual(column['default'], "'unisex'")
            self.assert_preserved(connection)

    def test_postgres_schema_sql_and_migration_chain(self):
        output = io.StringIO()
        context = MigrationContext.configure(dialect_name='postgresql', opts={'as_sql': True, 'output_buffer': output})
        with Operations.context(context):
            migration.upgrade()
        sql = output.getvalue()
        self.assertIn('ALTER TABLE fit_profiles ALTER COLUMN gender_target DROP NOT NULL', sql)
        self.assertIn('ALTER TABLE fit_profiles ALTER COLUMN gender_target DROP DEFAULT', sql)
        self.assertNotIn('UPDATE ', sql)
        self.assertEqual(migration.revision, '20261015_0027')
        self.assertEqual(migration.down_revision, '20261015_0026')
