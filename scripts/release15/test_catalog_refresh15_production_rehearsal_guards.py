"""Offline isolated-server guards, using metadata fixtures only; no execution."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

SOURCE = Path(__file__).with_name('catalog_refresh15_production_import_rehearsal_remote.py')
EXERCISE = '3c905d3909274276a9b9ca8b9cb8dfe2'


def load():
    spec = importlib.util.spec_from_file_location('isolated_server_guards', SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class IsolatedServerGuards(unittest.TestCase):
    def setUp(self):
        self.m = load()
        self.m.ARGS = SimpleNamespace(exercise_id=EXERCISE)
        self.m.NETWORK = 'modish-prodproof-' + EXERCISE
        self.m.NETWORK_ID = 'b' * 64
        self.m.PG = 'modish_prodproof_pg_' + EXERCISE
        self.m.PG_ID = 'c' * 64
        self.m.BASE = SimpleNamespace(DOCKER=['docker', '--host', 'unix:///var/run/docker.sock'],
            PROD={'modish_backend': ('id_backend', 'image_backend'), 'modish_postgres': ('id_pg', 'image_pg')},
            run=Mock(), PG_ENV={'PGHOST', 'PGPORT', 'PGOPTIONS'})
        self.m.S = Path('/private/fixture')

    def metadata(self, kind='import', running=False):
        m = self.m
        name = 'modish_prodproof_' + kind + '_' + EXERCISE
        identity = 'd' * 64
        if kind == 'postgres':
            name, identity, image = m.PG, m.PG_ID, 'image_pg'
        else:
            image = m.IMAGE
        memory = (512 if kind == 'postgres' else (768 if kind == 'migrate' else 1536)) * m.MIB
        cpu = 500000000 if kind == 'postgres' else (750000000 if kind == 'migrate' else 1000000000)
        value = {'Id': identity, 'Name': '/' + name, 'Image': image,
            'Config': {'Labels': {'org.modish.production_import_exercise': EXERCISE, 'org.modish.exercise_kind': kind}},
            'NetworkSettings': {'Networks': {m.NETWORK: {'NetworkID': m.NETWORK_ID, 'Aliases': ['postgres']}}},
            'State': {'Running': running, 'ExitCode': 0, 'OOMKilled': False},
            'Mounts': [], 'HostConfig': {'PortBindings': {}, 'Privileged': False, 'ReadonlyRootfs': True,
                'NetworkMode': m.NETWORK, 'PidMode': '', 'IpcMode': 'private', 'Devices': [],
                'RestartPolicy': {'Name': 'no'}, 'SecurityOpt': ['no-new-privileges'], 'Memory': memory,
                'MemorySwap': memory, 'NanoCpus': cpu, 'PidsLimit': 128, 'CapDrop': ['ALL'],
                'CapAdd': ['CHOWN', 'SETUID', 'SETGID', 'DAC_OVERRIDE', 'FOWNER'] if kind == 'postgres' else []}}
        return name, identity, image, value

    def guard(self, value, kind='import', running=None, mounts=None):
        name, identity, image, unused = self.metadata(kind)
        with patch.object(self.m, 'inspect', return_value=value):
            return self.m.container_guard(name, identity, image, kind, mounts or {}, running=running)

    def test_import_has_no_side_effects(self):
        module = load()
        self.assertIsNone(module.BASE)
        self.assertIsNone(module.S)
        self.assertIsNone(module.PG_ID)
        self.assertIsNone(module.ACTIVE_WORKER)

    def test_container_with_exact_metadata_passes(self):
        for kind in ('postgres', 'migrate', 'import'):
            self.guard(self.metadata(kind)[3], kind)

    def test_pgdata_contract_is_exact_bind_root(self):
        self.assertEqual(self.m.PGDATA_PATH, '/var/lib/postgresql/data')
        obj = self.metadata(kind='postgres', running=True)[3]
        obj['Config']['Env'] = ['POSTGRES_USER=' + self.m.ADMIN, 'POSTGRES_DB=postgres',
            'PGDATA=' + self.m.PGDATA_PATH]
        with patch.object(self.m, 'container_guard', return_value=obj):
            self.m.pg_guard()

    def test_nested_or_foreign_pgdata_rejected(self):
        for value in ('/var/lib/postgresql/data/pgdata', '/tmp/pgdata', ''):
            obj = self.metadata(kind='postgres', running=True)[3]
            obj['Config']['Env'] = ['POSTGRES_USER=' + self.m.ADMIN, 'POSTGRES_DB=postgres', 'PGDATA=' + value]
            with self.subTest(value=value), patch.object(self.m, 'container_guard', return_value=obj):
                with self.assertRaisesRegex(RuntimeError, 'isolated_server_admin_and_alias_required'):
                    self.m.pg_guard()

    def test_running_container_requires_pinned_network_id(self):
        value = self.metadata(running=True)[3]
        value['NetworkSettings']['Networks'][self.m.NETWORK]['NetworkID'] = ''
        with self.assertRaisesRegex(RuntimeError, 'owned_single_network_exercise_container_required'):
            self.guard(value)
        value = self.metadata(running=False)[3]
        value['NetworkSettings']['Networks'][self.m.NETWORK]['NetworkID'] = ''
        self.guard(value)

    def test_production_network_or_second_network_rejected(self):
        for extra in ('modish_default', 'modish-foundation-test', 'bridge'):
            value = self.metadata()[3]
            value['NetworkSettings']['Networks'][extra] = {'NetworkID': 'foreign'}
            with self.subTest(extra=extra), self.assertRaisesRegex(RuntimeError, 'owned_single_network_exercise_container_required'):
                self.guard(value)

    def test_wrong_id_image_or_label_rejected(self):
        for key, value in (('Id', 'foreign'), ('Image', 'foreign'), ('Name', '/foreign')):
            obj = self.metadata()[3]
            obj[key] = value
            with self.subTest(key=key), self.assertRaisesRegex(RuntimeError, 'owned_single_network_exercise_container_required'):
                self.guard(obj)
        obj = self.metadata()[3]
        obj['Config']['Labels']['org.modish.production_import_exercise'] = 'a' * 32
        with self.assertRaisesRegex(RuntimeError, 'owned_single_network_exercise_container_required'):
            self.guard(obj)

    def test_host_ports_privilege_or_writable_root_denied(self):
        for key, value in (('PortBindings', {'5432/tcp': [{}]}), ('Privileged', True), ('ReadonlyRootfs', False),
                ('NetworkMode', 'host'), ('PidMode', 'host'), ('IpcMode', 'host'), ('Devices', [{}]), ('SecurityOpt', [])):
            obj = self.metadata()[3]
            obj['HostConfig'][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(RuntimeError, 'bounded_private_exercise_container_required'):
                self.guard(obj)

    def test_missing_or_expanded_resource_limits_rejected(self):
        for key in ('Memory', 'MemorySwap', 'NanoCpus', 'PidsLimit'):
            obj = self.metadata()[3]
            obj['HostConfig'][key] = 0
            with self.subTest(key=key), self.assertRaisesRegex(RuntimeError, 'exact_exercise_resource_limits_required'):
                self.guard(obj)

    def test_added_worker_capabilities_rejected(self):
        obj = self.metadata()[3]
        obj['HostConfig']['CapAdd'] = ['SYS_ADMIN']
        with self.assertRaisesRegex(RuntimeError, 'exact_exercise_capabilities_required'):
            self.guard(obj)
        obj = self.metadata()[3]
        obj['HostConfig']['CapDrop'] = []
        with self.assertRaisesRegex(RuntimeError, 'exact_exercise_capabilities_required'):
            self.guard(obj)

    def test_unexpected_docker_socket_or_rw_helper_mount_rejected(self):
        for value in ({'Type': 'bind', 'Destination': '/var/run/docker.sock', 'Source': '/var/run/docker.sock', 'RW': True},
                {'Type': 'bind', 'Destination': '/reviewed/importer.py', 'Source': '/private/importer.py', 'RW': True},
                {'Type': 'volume', 'Destination': '/data', 'Name': 'arbitrary'}):
            obj = self.metadata()[3]
            obj['Mounts'] = [value]
            with self.subTest(value=value), self.assertRaisesRegex(RuntimeError, 'exact_exercise_mounts_required'):
                self.guard(obj)

    def network(self):
        return {'Id': self.m.NETWORK_ID, 'Name': self.m.NETWORK, 'Internal': True, 'Driver': 'bridge',
            'Labels': {'org.modish.production_import_exercise': EXERCISE},
            'Containers': {self.m.PG_ID: {'Name': self.m.PG}}}

    def test_new_internal_network_and_owned_pg_pass(self):
        with patch.object(self.m, 'command', return_value=json.dumps([self.network()]).encode()), \
                patch.object(self.m, 'inspect', return_value={'NetworkSettings': {'Networks': {'modish_default': {}}}}):
            self.m.network_guard()

    def test_external_network_denied(self):
        value = self.network()
        value['Internal'] = False
        with patch.object(self.m, 'command', return_value=json.dumps([value]).encode()):
            with self.assertRaisesRegex(RuntimeError, 'new_internal_exercise_network_required'):
                self.m.network_guard()

    def test_foreign_container_even_with_similar_name_denied(self):
        value = self.network()
        value['Containers']['foreign'] = {'Name': self.m.PG + '-foreign'}
        with patch.object(self.m, 'command', return_value=json.dumps([value]).encode()):
            with self.assertRaisesRegex(RuntimeError, 'unexpected_network_member_requires_review'):
                self.m.network_guard()

    def test_production_member_on_exercise_network_denied(self):
        with patch.object(self.m, 'command', return_value=json.dumps([self.network()]).encode()), \
                patch.object(self.m, 'inspect', return_value={'NetworkSettings': {'Networks': {self.m.NETWORK: {}}}}):
            with self.assertRaisesRegex(RuntimeError, 'production_must_be_off_exercise_network'):
                self.m.network_guard()

    def test_sql_cannot_run_without_guarded_new_postgres(self):
        with patch.object(self.m, 'pg_guard', side_effect=RuntimeError('wrong server')), patch.object(self.m, 'command') as command:
            with self.assertRaisesRegex(RuntimeError, 'wrong server'):
                self.m.sql('SELECT 1;')
            command.assert_not_called()

    def test_sql_targets_exact_new_id_not_production_name(self):
        with patch.object(self.m, 'pg_guard'), patch.object(self.m, 'command', return_value=b'1') as command:
            self.assertEqual(self.m.sql('SELECT 1;'), '1')
            argv = command.call_args.args[1]
            self.assertIn(self.m.PG_ID, argv)
            self.assertNotIn('modish_postgres', argv)
            self.assertIn('PGOPTIONS=-c statement_timeout=180000 -c lock_timeout=5000 -c timezone=UTC -c default_transaction_read_only=on', argv)

    def test_timeout_stop_requires_exact_owned_metadata(self):
        m = self.m
        name, identity, image, obj = self.metadata(running=True)
        m.ACTIVE_WORKER = (name, identity, 'import', {})
        m.DEADLINE = 0
        with patch.object(m, 'container_guard', side_effect=RuntimeError('foreign')), patch.object(m, 'command') as command:
            with self.assertRaisesRegex(RuntimeError, 'foreign'):
                m.stop_worker()
            command.assert_not_called()
        self.assertEqual(m.DEADLINE, 0)

    def test_expired_deadline_still_allows_only_guarded_own_stop(self):
        m = self.m
        name, identity, image, obj = self.metadata(running=True)
        m.ACTIVE_WORKER = (name, identity, 'import', {})
        m.DEADLINE = 0
        with patch.object(m, 'container_guard', return_value=obj), patch.object(m, 'command') as command:
            m.stop_worker()
            self.assertEqual(command.call_args.args[1], [*m.BASE.DOCKER, 'stop', '--time', '10', identity])
            self.assertEqual(command.call_args.kwargs['timeout'], 20)
        self.assertEqual(m.DEADLINE, 0)

    def test_command_global_timeout_prevents_execution(self):
        self.m.DEADLINE = 0
        with self.assertRaisesRegex(RuntimeError, 'exercise_total_timeout'):
            self.m.command('never', ['docker', 'start', 'never'])
        self.m.BASE.run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
