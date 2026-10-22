"""Offline negative guards for phase1; no Docker, stop, SQL or file mutations."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

SOURCE = Path(__file__).with_name('build15_activation_final_backup_remote.py')
PIN = '8b54caa30bf96036b8c9e28108794dc5ead29ce654b0491d0653fb2ee22acfba'


def load():
    fake = ModuleType('fcntl')
    fake.flock = Mock()
    fake.LOCK_EX, fake.LOCK_NB = 2, 4
    spec = importlib.util.spec_from_file_location('phase1_offline_guards', SOURCE)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'fcntl': fake}):
        spec.loader.exec_module(module)
    return module


class FinalBackupGuards(unittest.TestCase):
    def setUp(self):
        self.m = load()

    def test_reviewed_phase1_source_pin(self):
        self.assertEqual(hashlib.sha256(SOURCE.read_bytes()).hexdigest(), PIN)

    def test_import_has_no_runtime_side_effects(self):
        m = self.m
        self.assertIsNone(m.BASE)
        self.assertIsNone(m.PLAN)
        self.assertIsNone(m.ROOT)
        self.assertIsNone(m.STATE)
        self.assertIsNone(m.LOCK)
        self.assertFalse(m.BACKEND_STOP_ATTEMPTED)
        self.assertFalse(m.BACKEND_STOPPED)

    def arguments(self, token=None, plan_id=None, plan_sha=None):
        return ['phase1', '--root', '/should/not/be/read', '--plan-id', plan_id or '3c905d3909274276a9b9ca8b9cb8dfe2',
            '--plan-sha256', plan_sha or 'f' * 64, '--writer-review-sha256', 'e' * 64,
            '--proxy-plan-sha256', 'd' * 64,
            '--approval-token', token or 'not-approved']

    def test_explicit_approval_rejected_before_files_or_docker(self):
        m = self.m
        with patch.object(sys, 'argv', self.arguments()), patch.object(m.os, 'name', 'posix'), \
                patch.object(m.os, 'geteuid', lambda: 0, create=True), patch.object(m, 'bounded_private') as read:
            with self.assertRaisesRegex(RuntimeError, 'pinned_plan_and_explicit_owner_approval_required'):
                m.main()
            read.assert_not_called()
        self.assertFalse(m.BACKEND_STOP_ATTEMPTED)

    def test_missing_plan_hash_rejected_before_files_or_docker(self):
        m = self.m
        with patch.object(sys, 'argv', self.arguments(token=m.APPROVAL, plan_sha='wrong')), \
                patch.object(m.os, 'name', 'posix'), patch.object(m.os, 'geteuid', lambda: 0, create=True):
            with self.assertRaisesRegex(RuntimeError, 'pinned_plan_and_explicit_owner_approval_required'):
                m.main()
        self.assertFalse(m.BACKEND_STOP_ATTEMPTED)

    def test_non_uuid4_plan_rejected(self):
        m = self.m
        with patch.object(sys, 'argv', self.arguments(token=m.APPROVAL, plan_id='a' * 32)), \
                patch.object(m.os, 'name', 'posix'), patch.object(m.os, 'geteuid', lambda: 0, create=True):
            with self.assertRaisesRegex(RuntimeError, 'uuid4_plan_required'):
                m.main()

    def test_production_sql_write_guard_precedes_inspection(self):
        m = self.m
        m.BASE = Mock()
        with self.assertRaisesRegex(RuntimeError, 'production_sql_writes_forbidden'):
            m.readonly_sql('CREATE ROLE forbidden;', write_test=True, test=False)
        m.BASE.inspect.assert_not_called()
        m.BASE.run.assert_not_called()

    def test_wrong_actual_api_identity_denies_continuation(self):
        m = self.m
        m.BASE = SimpleNamespace(DOCKER=['docker'], PROD={'modish_backend': ('id', 'image')})
        value = {'actual_database_matches': True, 'actual_role_matches': True, 'port_matches': True,
            'read_only': False, 'revision0025': True}
        with patch.object(m, 'command', return_value=json.dumps(value).encode()):
            with self.assertRaisesRegex(RuntimeError, 'actual_api_credentials_readonly_probe_required'):
                m.actual_api_database_probe()

    def test_unknown_other_sessions_deny_fence(self):
        with patch.object(self.m, 'readonly_sql', return_value='1'):
            with self.assertRaisesRegex(RuntimeError, 'unaccounted_database_client_sessions_present'):
                self.m.assert_no_other_sessions()

    def test_exact_zero_other_sessions_required(self):
        for value in ('', '00', '-1', '0\n1'):
            with self.subTest(value=value), patch.object(self.m, 'readonly_sql', return_value=value):
                with self.assertRaisesRegex(RuntimeError, 'unaccounted_database_client_sessions_present'):
                    self.m.assert_no_other_sessions()

    def test_changed_or_running_backend_denies_stopped_proof(self):
        m = self.m
        for identity, image, running in (('wrong', 'image', False), ('id', 'wrong', False), ('id', 'image', True)):
            m.BASE = SimpleNamespace(PROD={'modish_backend': ('id', 'image')},
                inspect=Mock(return_value={'Id': identity, 'Image': image, 'State': {'Running': running}}))
            with self.subTest(identity=identity, image=image, running=running):
                with self.assertRaisesRegex(RuntimeError, 'pinned_old_backend_must_remain_stopped'):
                    m.backend_still_stopped('test')

    def test_injected_or_unbounded_table_names_never_reach_sql(self):
        for value in ('', 'products; DROP TABLE users', '\n'.join('t' + str(i) for i in range(101))):
            with self.subTest(value=value), patch.object(self.m, 'readonly_sql', return_value=value) as sql:
                with self.assertRaisesRegex(RuntimeError, 'bounded_application_tables_required'):
                    self.m.source_integrity()
                self.assertEqual(sql.call_count, 1)

    def test_invalid_aggregate_hash_or_count_rejected(self):
        for value in ({'count': -1, 'rows': 'f' * 32}, {'count': 1, 'rows': 'private text'}):
            with self.subTest(value=value), patch.object(self.m, 'readonly_sql', side_effect=['products', json.dumps(value)]):
                with self.assertRaisesRegex(RuntimeError, 'bounded_full_row_proof_required'):
                    self.m.source_integrity()

    def test_expired_global_deadline_prevents_command(self):
        m = self.m
        m.BASE = Mock()
        m.DEADLINE = 0
        with self.assertRaisesRegex(RuntimeError, 'final_backup_total_deadline_exceeded'):
            m.command('never', ['docker', 'stop', 'never'])
        m.BASE.run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
