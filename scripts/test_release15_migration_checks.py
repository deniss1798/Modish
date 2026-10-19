"""Offline isolation checks; the separate runner supplies real PG evidence."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts import check_release15_migrations as checks


class Release15MigrationGuardTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name).resolve()
        self.cluster = self.workspace / '.tmp/alpha12-pg-synthetic'
        self.cluster.mkdir(parents=True)
        self.runtime = self.workspace / '.tmp/runtime.json'
        self.metadata = {'root': str(self.cluster), 'data': str(self.cluster / 'data'), 'port': 55439}

    def load(self, **updates):
        self.runtime.write_text(json.dumps(self.metadata | updates), encoding='utf-8')
        return checks.load_runtime(self.runtime, workspace=self.workspace)

    def test_expected_workspace_fixture_is_accepted(self):
        self.assertEqual(self.load(), {'data': self.cluster / 'data', 'port': 55439})

    def test_runtime_outside_tmp_is_rejected_before_connection(self):
        with self.assertRaisesRegex(checks.MigrationCheckError, 'runtime_outside'):
            checks.load_runtime(self.workspace / 'runtime.json', workspace=self.workspace)

    def test_metadata_cannot_redirect_to_system_port_or_string_port(self):
        for port in (5432, '55439', True):
            with self.subTest(port=port), self.assertRaisesRegex(checks.MigrationCheckError, 'port'):
                self.load(port=port)

    def test_metadata_cannot_redirect_to_system_data_or_nested_cluster(self):
        for update in ({'data': str(self.workspace / 'system/data')},
                       {'root': str(self.workspace / '.tmp/other')},
                       {'root': str(self.workspace / '.tmp/nested/alpha12-pg-x')},
                       {'root': str(self.workspace / 'alpha12-pg-x')}):
            with self.subTest(update=update), self.assertRaises(checks.MigrationCheckError):
                self.load(**update)

    def test_cleared_environment_drops_provider_auth_and_database_secrets(self):
        with patch.dict(os.environ, {'DATABASE_URL': 'SYNTHETIC_REJECTED_DATABASE_VALUE',
                                    'JWT_SECRET': 'SYNTHETIC_REJECTED_KEY_VALUE',
                                    'OPENAI_API_KEY': 'SYNTHETIC_REJECTED_PROVIDER_VALUE',
                                    'PGPASSWORD': 'SYNTHETIC_REJECTED_PASSWORD_VALUE',
                                    'PATH': 'runtime-path', 'SYSTEMROOT': 'runtime-root'}, clear=True):
            environment = checks.isolated_environment('sqlite://')
        self.assertEqual(environment['DATABASE_URL'], 'sqlite://')
        self.assertEqual(environment['PYTHON_DOTENV_DISABLED'], '1')
        self.assertEqual(environment['SYSTEMROOT'], 'runtime-root')
        self.assertEqual(environment['PATH'], 'runtime-path')
        self.assertNotIn('OPENAI_API_KEY', environment)
        self.assertNotIn('PGPASSWORD', environment)
        self.assertNotIn('SYNTHETIC_REJECTED', str(environment))

    def test_actual_server_mismatch_prevents_any_ddl(self):
        runtime = self.load()
        for values in ([str(self.workspace / 'other'), '55439', 'postgres'],
                       [str(runtime['data']), '5432', 'postgres'],
                       [str(runtime['data']), '55439', 'modish']):
            with self.subTest(values=values):
                connection = Mock()
                connection.scalar.side_effect = values
                admin = Mock()
                admin.begin.return_value.__enter__ = Mock(return_value=connection)
                admin.begin.return_value.__exit__ = Mock(return_value=False)
                with self.assertRaises(checks.MigrationCheckError):
                    with checks.isolated_schema(admin, runtime):
                        self.fail('unsafe schema was yielded')
                connection.execute.assert_not_called()

    def test_generated_schema_cleanup_is_verified_after_scenario_failure(self):
        runtime = self.load()
        connection = Mock()
        connection.scalar.side_effect = [str(runtime['data']), '55439', 'postgres'] * 2
        admin = Mock()
        admin.begin.return_value.__enter__ = Mock(return_value=connection)
        admin.begin.return_value.__exit__ = Mock(return_value=False)
        with self.assertRaisesRegex(RuntimeError, 'synthetic_scenario_failure'):
            with checks.isolated_schema(admin, runtime) as schema:
                self.assertRegex(schema, checks.SCHEMA_PATTERN)
                raise RuntimeError('synthetic_scenario_failure')
        statements = [str(call.args[0]) for call in connection.execute.call_args_list]
        self.assertEqual(statements, [f'CREATE SCHEMA {schema}', f'DROP SCHEMA {schema} CASCADE'])
        self.assertEqual(connection.scalar.call_count, 6)

    def test_url_is_fixed_loopback_and_search_path_has_no_public_fallback(self):
        schema = 'release15_migrations_' + 'a' * 32
        url = checks.fixture_url(schema)
        self.assertEqual((url.host, url.port, url.database, url.username),
                         ('127.0.0.1', 55439, 'postgres', 'modish_alpha_fixture'))
        self.assertIsNone(url.password)
        self.assertIn(f'search_path={schema} ', url.query['options'])
        self.assertNotIn('public', url.query['options'])
        self.assertEqual(url.query['connect_timeout'], '3')
        for invalid in ('public', schema + '; DROP SCHEMA public', schema + ',public'):
            with self.subTest(invalid=invalid), self.assertRaises(checks.MigrationCheckError):
                checks.fixture_url(invalid)

    def test_alembic_receives_safe_plain_url_and_generated_process_search_path(self):
        schema = 'release15_migrations_' + 'b' * 32
        seen = {}

        def fake_upgrade(config, revision):
            seen.update(os.environ)
            self.assertEqual(revision, checks.OLD_REVISION)
            # Match unchanged env.py's ConfigParser call, including its '%'
            # interpolation behavior; no actual connection is opened here.
            config.set_main_option('sqlalchemy.url', os.environ['DATABASE_URL'])
            self.assertNotIn('%', config.get_main_option('sqlalchemy.url'))

        with patch.dict(os.environ, {'PGOPTIONS': '-c search_path=public',
                                    'DATABASE_URL': 'sensitive-production-url'}, clear=True), \
                patch('alembic.command.upgrade', side_effect=fake_upgrade):
            checks.migrate(checks.fixture_url(schema), 'upgrade', checks.OLD_REVISION)
        self.assertIn(f'search_path={schema} ', seen['PGOPTIONS'])
        self.assertNotIn('public', seen['PGOPTIONS'])
        self.assertNotIn('sensitive', str(seen))
        self.assertIn('127.0.0.1:55439/postgres', seen['DATABASE_URL'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
