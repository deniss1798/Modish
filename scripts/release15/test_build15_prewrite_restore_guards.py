"""Offline pre-writes recovery guards; metadata/ledger fixtures, no SQL/Docker."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

SOURCE = Path(__file__).with_name('build15_prewrite_restore_remote.py')
RECOVERY = '3c905d3909274276a9b9ca8b9cb8dfe2'


def load():
    fake = ModuleType('fcntl')
    fake.flock = Mock(); fake.LOCK_EX, fake.LOCK_NB = 2, 4
    spec = importlib.util.spec_from_file_location('prewrite_restore_offline', SOURCE)
    value = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'fcntl': fake}):
        spec.loader.exec_module(value)
    return value


class PrewriteRecoveryGuards(unittest.TestCase):
    def setUp(self):
        self.m = load()
        self.m.ARGS = SimpleNamespace(recovery_id=RECOVERY, plan_sha256='f' * 64)

    def test_import_has_no_side_effects(self):
        value = load()
        self.assertIsNone(value.BASE)
        self.assertFalse(value.RENAME_ATTEMPTED)
        self.assertFalse(value.OPEN_ATTEMPTED)
        self.assertFalse(value.STAGE_CREATED)
        self.assertEqual(value.OWNED_APIS, [])

    def test_wrong_approval_denied_before_artifacts(self):
        m = self.m
        argv = ['recovery']
        for key, value in {'root': '/never-read', 'plan-sha256': 'f' * 64, 'final-backup-report-sha256': 'e' * 64,
                'source-integrity-sha256': 'd' * 64, 'proxy-plan-sha256': 'c' * 64, 'recovery-id': RECOVERY,
                'approval-token': 'EXAMPLE_WRONG_TOKEN'}.items():
            argv += ['--' + key, value]
        with patch.object(sys, 'argv', argv), patch.object(m.os, 'name', 'posix'), \
                patch.object(m.os, 'geteuid', lambda: 0, create=True), patch.object(m, 'private') as read:
            with self.assertRaisesRegex(RuntimeError, 'explicit_recovery_approval_and_pinned_reports_required'):
                m.main()
            read.assert_not_called()

    def failure(self):
        return {'operation': 'build15_approved_production_activation', 'status': 'failed',
            'public_writes_may_exist': False, 'import_outcome_uncertain_requires_review': False}

    def test_every_public_open_marker_denies_recovery(self):
        for filename in ('phase-23-writes_open_about_to_start.json', 'proxy-open-intent.json', 'activation-report.json'):
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / filename).write_text('{}')
                with self.subTest(filename=filename), self.assertRaisesRegex(RuntimeError, 'public_writes_history_forbids_prewrite_restore'):
                    self.m.closed_history(root, self.failure())

    def test_nested_public_open_marker_also_denies(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'nested').mkdir()
            (root / 'nested' / 'proxy-open-intent.json').write_text('{}')
            with self.assertRaisesRegex(RuntimeError, 'public_writes_history_forbids_prewrite_restore'):
                self.m.closed_history(root, self.failure())

    def test_failure_indicating_public_writes_denied(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(RuntimeError, 'known_prewrite_continuation_failure_required'):
                self.m.closed_history(Path(temporary), {**self.failure(), 'public_writes_may_exist': True})

    def test_unknown_commit_requires_separate_manual_resolution(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(RuntimeError, 'unknown_import_commit_requires_separate_manual_resolution'):
                self.m.closed_history(Path(temporary), {**self.failure(), 'import_outcome_uncertain_requires_review': True})

    def test_commit_intent_without_durable_commit_denied(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'production-import-outcome').mkdir()
            (root / 'production-import-outcome' / 'commit-intent.json').write_text('{}')
            with self.assertRaisesRegex(RuntimeError, 'unknown_import_commit_requires_separate_manual_resolution'):
                self.m.closed_history(root, self.failure())

    def test_explicit_known_prewrite_failure_allowed(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.m.closed_history(Path(temporary), self.failure())

    def test_arbitrary_database_rejected_before_inspect_or_sql(self):
        with patch.object(self.m, 'stable_infrastructure') as inspect, patch.object(self.m, 'command') as command:
            with self.assertRaisesRegex(RuntimeError, 'fixed_recovery_database_required'):
                self.m.sql('SELECT 1', database='other_database')
            inspect.assert_not_called(); command.assert_not_called()

    def test_sql_uses_pinned_production_pg_and_readonly_by_default(self):
        m = self.m
        m.BASE = SimpleNamespace(DOCKER=['docker', '--host', 'unix:///var/run/docker.sock'], PROD={'modish_postgres': ('pinned_pg_id', 'pinned_image')})
        with patch.object(m, 'stable_infrastructure'), patch.object(m, 'command', return_value=b'1') as command:
            self.assertEqual(m.sql('SELECT 1'), '1')
            argv = command.call_args.args[1]
            self.assertIn('pinned_pg_id', argv)
            self.assertNotIn('modish_postgres', argv)
            self.assertTrue(any('default_transaction_read_only=on' in item for item in argv))

    def test_no_client_check_covers_live_restored_and_failed_databases(self):
        with patch.object(self.m, 'sql', return_value='0') as sql:
            self.m.no_clients()
            value = sql.call_args.args[0]
            self.assertIn("'modish'", value)
            self.assertIn('modish_failed15_' + RECOVERY, value)
            self.assertIn('modish_recovery15_' + RECOVERY, value)
        with patch.object(self.m, 'sql', return_value='1'):
            with self.assertRaisesRegex(RuntimeError, 'writers_or_unaccounted_database_clients_present'):
                self.m.no_clients()

    def test_recommendation_revision_change_denies_restore(self):
        with patch.object(self.m, 'sql', side_effect=['20261015_0028', '1', '1']), patch.object(self.m, 'table_proof') as proof:
            with self.assertRaisesRegex(RuntimeError, 'user_recommendation_changes_forbid_backup_restore'):
                self.m.current_user_data_unchanged('restored')
            proof.assert_not_called()

    def test_missing_revision_column_on0028_denies_restore(self):
        with patch.object(self.m, 'sql', side_effect=['20261015_0028', '0']):
            with self.assertRaisesRegex(RuntimeError, 'expected_revision_column_required'):
                self.m.current_user_data_unchanged('restored')

    def test_new_user_or_unaffected_record_denies_restore(self):
        with patch.object(self.m, 'sql', side_effect=['20261015_0028', '1', '0']), \
                patch.object(self.m, 'table_proof', side_effect=[{'users': {'count': 17}}, {'users': {'count': 18}}]):
            with self.assertRaisesRegex(RuntimeError, 'new_user_or_unaffected_data_forbids_backup_restore'):
                self.m.current_user_data_unchanged('restored')

    def test_unknown_schema_denies_restore(self):
        with patch.object(self.m, 'sql', return_value='unknown'):
            with self.assertRaisesRegex(RuntimeError, 'known_failed_migration_revision_required'):
                self.m.current_user_data_unchanged('restored')

    def test_old_api_not_created_on0028(self):
        with patch.object(self.m, 'sql', return_value='20261015_0028'), patch.object(self.m, 'command') as command:
            with self.assertRaisesRegex(RuntimeError, 'legacy_api_requires_restored_schema0025'):
                self.m.create_legacy_api('public')
            command.assert_not_called()

    def api(self):
        m = self.m
        m.BASE = SimpleNamespace(PROD={'modish_backend': ('old_api_id', 'old_image')})
        m.OLD_RUNTIME = {'Mounts': [], 'HostConfig': {'ReadonlyRootfs': False}}
        env = {'EXAMPLE_ENV': 'SYNTHETIC_VALUE'}
        item = {'id': 'new_api_id', 'name': 'recovery_api', 'kind': 'public',
            'environment_sha256': hashlib.sha256(json.dumps(env, sort_keys=True).encode()).hexdigest()}
        obj = {'Id': item['id'], 'Name': '/' + item['name'], 'Image': 'old_image',
            'Config': {'Entrypoint': ['python'], 'Cmd': m.COMMAND, 'Labels': {'org.modish.prewrite_recovery': RECOVERY},
                'Env': ['EXAMPLE_ENV=SYNTHETIC_VALUE']}, 'NetworkSettings': {'Networks': {'modish_default': {}}},
            'Mounts': [], 'HostConfig': {'PortBindings': {'8000/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '8000'}]},
                'Privileged': False, 'ReadonlyRootfs': False, 'Memory': 1536 * 1024**2, 'MemorySwap': 1536 * 1024**2,
                'NanoCpus': 1000000000, 'PidsLimit': 128, 'CapDrop': ['ALL'], 'CapAdd': [], 'SecurityOpt': ['no-new-privileges'],
                'RestartPolicy': {'Name': 'no'}, 'PidMode': '', 'IpcMode': 'private', 'Devices': [], 'DeviceRequests': []}}
        return item, obj

    def test_exact_loopback_direct_legacy_api_passes(self):
        item, obj = self.api()
        with patch.object(self.m, 'inspect', return_value=obj):
            self.m.api_owned(item)

    def test_public_legacy_preserves_original_writable_root_for_uploads(self):
        item, obj = self.api()
        obj['HostConfig']['ReadonlyRootfs'] = True
        with patch.object(self.m, 'inspect', return_value=obj):
            with self.assertRaisesRegex(RuntimeError, 'exact_owned_schema0025_legacy_api_required'):
                self.m.api_owned(item)
        self.m.OLD_RUNTIME['HostConfig']['ReadonlyRootfs'] = True
        with patch.object(self.m, 'inspect', return_value=obj):
            self.m.api_owned(item)

    def test_shadow_legacy_is_readonly_even_when_public_baseline_is_writable(self):
        item, obj = self.api()
        item['kind'] = 'shadow'
        obj['HostConfig']['PortBindings'] = {}
        with patch.object(self.m, 'inspect', return_value=obj):
            with self.assertRaisesRegex(RuntimeError, 'exact_owned_schema0025_legacy_api_required'):
                self.m.api_owned(item)
        obj['HostConfig']['ReadonlyRootfs'] = True
        with patch.object(self.m, 'inspect', return_value=obj):
            self.m.api_owned(item)

    def test_public_ports_foreign_image_or_migrating_entrypoint_denied(self):
        for change in ('ports', 'image', 'entrypoint', 'network', 'memory'):
            item, obj = self.api()
            if change == 'ports':obj['HostConfig']['PortBindings']['8000/tcp'][0]['HostIp'] = '0.0.0.0'
            if change == 'image':obj['Image'] = self.m.IMAGE
            if change == 'entrypoint':obj['Config']['Entrypoint'] = ['/app/scripts/entrypoint.sh']
            if change == 'network':obj['NetworkSettings']['Networks']['foreign'] = {}
            if change == 'memory':obj['HostConfig']['Memory'] = 0
            with self.subTest(change=change), patch.object(self.m, 'inspect', return_value=obj):
                with self.assertRaisesRegex(RuntimeError, 'exact_owned_schema0025_legacy_api_required'):
                    self.m.api_owned(item)

    def test_changed_jwt_or_database_environment_denied(self):
        item, obj = self.api()
        obj['Config']['Env'] = ['EXAMPLE_ENV=CHANGED_SYNTHETIC_VALUE']
        with patch.object(self.m, 'inspect', return_value=obj):
            with self.assertRaisesRegex(RuntimeError, 'legacy_api_environment_must_match'):
                self.m.api_owned(item)

    def test_changed_storage_mount_denied(self):
        item, obj = self.api()
        obj['Mounts'] = [{'Type': 'bind', 'Source': '/other', 'Destination': '/data', 'RW': True}]
        with patch.object(self.m, 'inspect', return_value=obj):
            with self.assertRaisesRegex(RuntimeError, 'legacy_storage_preservation_required'):
                self.m.api_owned(item)

    def test_missing_backend_after_failed_replacement_is_not_an_inspect_bypass(self):
        m = self.m
        m.BASE = SimpleNamespace(DOCKER=['docker'])
        m.PLAN = {'plan_id': RECOVERY}
        with patch.object(m, 'command', side_effect=[b'', b'']) as command, \
                patch.object(m, 'inspect') as inspect, patch.object(m, 'phase') as phase:
            m.stop_deployment_writers()
            inspect.assert_not_called()
            phase.assert_called_once_with('backend_absence_verified', exact_name_inventory_empty=True)
            self.assertIn('name=^/modish_backend$', command.call_args_list[0].args[1])

    def test_ambiguous_backend_inventory_denies_before_inspect(self):
        m = self.m
        m.BASE = SimpleNamespace(DOCKER=['docker'])
        for output in (b'not-an-id\n', b'aabbccddeeff\n001122334455\n'):
            with self.subTest(output=output), patch.object(m, 'command', return_value=output), \
                    patch.object(m, 'inspect') as inspect:
                with self.assertRaisesRegex(RuntimeError, 'bounded_exact_backend_inventory_required'):
                    m.stop_deployment_writers()
                inspect.assert_not_called()

    def test_legacy_extra_devices_host_namespace_or_restart_denied(self):
        for key, value in (('Devices', [{'PathOnHost': '/dev/example'}]),
                ('DeviceRequests', [{'Driver': 'example'}]), ('PidMode', 'host'),
                ('IpcMode', 'host'), ('RestartPolicy', {'Name': 'always'})):
            item, obj = self.api()
            obj['HostConfig'][key] = value
            with self.subTest(key=key), patch.object(self.m, 'inspect', return_value=obj):
                with self.assertRaisesRegex(RuntimeError, 'exact_owned_schema0025_legacy_api_required'):
                    self.m.api_owned(item)

    def test_expired_deadline_prevents_command(self):
        self.m.BASE = SimpleNamespace(run=Mock())
        self.m.DEADLINE = 0
        with self.assertRaisesRegex(RuntimeError, 'recovery_total_deadline_exceeded'):
            self.m.command('never', ['docker', 'start', 'never'])
        self.m.BASE.run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
