"""Offline crash/ownership tests; never invokes Docker, SSH, or a database."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from uuid import uuid4

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts' / 'catalog_refresh17_host.py'
SPEC = importlib.util.spec_from_file_location('catalog_refresh17_host', SCRIPT)
host = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(host)

ENV = b'DATABASE_URL=postgresql://modish_user:private@modish_postgres/modish\n'


class FakeDocker:
    def __init__(self, supervisor):
        self.supervisor = supervisor
        self.objects = {}
        self.calls = []
        self.failure = None
        self.result_override = None
        self.inspection_mutation = None

    def inspect(self, kind, identity, *, absent_ok=False, timeout=30):
        config = self.supervisor.config
        if kind == 'image':
            return {'Id': config['image_id'], 'Config': {'Env': ['PATH=/usr/local/bin'], 'Labels': {
                'org.modish.release': 'build17', 'org.modish.source_commit': config['image_commit'],
                'org.modish.source_sha256': config['image_source_sha256'], 'org.modish.schema': host.SCHEMA,
                'org.modish.release_run': str(uuid4())}}}
        if kind == 'network':
            return {'Id': config['network_id'], 'Name': 'modish_default', 'Driver': 'bridge', 'Scope': 'local'}
        obj = self.objects.get(identity)
        if obj is None:
            obj = next((v for v in self.objects.values() if v['Name'] == '/' + identity), None)
        result = deepcopy(obj)
        if result and self.inspection_mutation:
            self.inspection_mutation(result)
        return result

    def object(self):
        supervisor = self.supervisor
        config, operation = supervisor.config, supervisor.state['operation']
        cid = host.digest(operation['id'].encode())
        return {'Id': cid, 'Name': '/' + operation['name'], 'Image': config['image_id'],
            'Config': {'Image': config['image_id'], 'Labels': supervisor.labels(operation),
                'Entrypoint': ['python'], 'Cmd': operation['command'], 'User': '0:0',
                'WorkingDir': '/app', 'StopSignal': 'SIGTERM', 'ExposedPorts': {'8000/tcp': {}},
                'Env': ['PATH=/usr/local/bin', *[k + '=' + v for k, v in host.environment(ENV).items()]]},
            'HostConfig': {'ReadonlyRootfs': True, 'NanoCpus': 1000000000,
                'Memory': 1536 * 1024**2, 'MemorySwap': 1536 * 1024**2, 'PidsLimit': 128,
                'CapDrop': ['ALL'], 'SecurityOpt': ['no-new-privileges:true'], 'Privileged': False,
                'Init': True, 'NetworkMode': config['network_id'], 'RestartPolicy': {'Name': 'no'},
                'AutoRemove': False},
            'Mounts': [{'Type': 'bind', 'Source': supervisor.state['artifact_directory'],
                'Destination': '/refresh', 'RW': True, 'Propagation': 'rprivate'}],
            'NetworkSettings': {'Networks': {'modish_default': {'NetworkID': config['network_id']}},
                'Ports': {'8000/tcp': None}},
            'State': {'Status': 'created', 'Running': False, 'ExitCode': 0, 'OOMKilled': False, 'Error': ''}}

    def call(self, arguments, **kwargs):
        self.calls.append(list(arguments))
        supervisor = self.supervisor
        if arguments[:2] == ['container', 'create']:
            # The create intent must already be durable before Docker sees it.
            persisted = json.loads(supervisor.path.read_text())
            assert persisted['operation']['create_intent'] is not None
            assert persisted['operation']['container_id'] is None
            obj = self.object()
            self.objects[obj['Id']] = obj
            if self.failure == 'create_ack_lost':
                raise host.HostRejected('host_docker_command_unavailable')
            return (obj['Id'] + '\n').encode()
        if arguments[:2] == ['container', 'start']:
            persisted = json.loads(supervisor.path.read_text())
            assert persisted['operation']['container_id'] == arguments[-1]
            assert persisted['operation']['start_intent'] is not None
            obj = self.objects[arguments[-1]]
            obj['State'].update(Status='exited', Running=False)
            if self.failure == 'start_ack_lost':
                obj['State'].update(Status='running', Running=True)
                raise host.HostRejected('host_docker_command_unavailable')
            return (arguments[-1] + '\n').encode()
        if arguments[:2] == ['container', 'logs']:
            phase = supervisor.state['operation']['phase']
            run_id = supervisor.state['run_id']
            folder = Path(supervisor.state['artifact_directory']) / ('run-' + run_id)
            if phase == 'prepare':
                folder.mkdir(mode=0o700)
                prepared = {'run_id': run_id, 'schema': host.SCHEMA, 'mode': 'production',
                    'status': 'catalogue_refresh_prepared', 'snapshot_sha256': 'a' * 64, 'schema_version': 1}
                raw = host.encoded(prepared)
                (folder / 'prepared.json').write_bytes(raw)
                result = {**prepared, 'directory': '/refresh/run-' + run_id,
                    'database_writes': False, 'prepared_sha256': host.digest(raw)}
            else:
                result = {'status': 'catalogue_refresh_complete', 'run_id': str(host.UUID(run_id)),
                    'snapshot_sha256': 'a' * 64}
                (folder / 'publication.json').write_bytes(host.encoded(
                    {'status': 'complete', 'pending': None, 'run_id': str(host.UUID(run_id)),
                        'snapshot_sha256': 'a' * 64}))
            if self.result_override:
                result.update(self.result_override)
            return host.encoded(result)
        if arguments[:2] == ['container', 'stop']:
            self.objects[arguments[-1]]['State'].update(Status='exited', Running=False)
            return b'stopped\n'
        if arguments[:2] == ['container', 'rm']:
            del self.objects[arguments[-1]]
            return b'removed\n'
        raise AssertionError(arguments)


class HostTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.env = self.root / 'runtime.env'
        self.env.write_bytes(ENV)
        self.config = {'version': 1, 'image_id': 'sha256:' + '1' * 64, 'image_commit': '2' * 40,
            'image_source_sha256': '3' * 64, 'network_name': 'modish_default', 'network_id': '4' * 64,
            'env_file': str(self.env), 'env_sha256': host.digest(ENV), 'workroot': str(self.root),
            'config_sha256': '5' * 64}
        self.clock_value = 1000.0
        self.addCleanup(patch.stopall)
        patch.object(host, 'private_directory', side_effect=lambda p: Path(p).resolve()).start()
        patch.object(host, 'private_bytes', side_effect=lambda p, **kw: Path(p).read_bytes()).start()
        patch.object(host, 'save_private', side_effect=lambda p, v: Path(p).write_bytes(host.encoded(v))).start()
        patch.object(host, 'sync_directory').start()
        self.supervisor = host.Supervisor(self.config, clock=lambda: self.clock_value, sleep=lambda _: None)
        self.docker = FakeDocker(self.supervisor)
        self.supervisor.docker = self.docker

    def start_prepared(self):
        self.supervisor.new_run()
        self.supervisor.worker('prepare')

    def test_two_phases_use_detached_start_and_complete_durable_evidence(self):
        result = self.supervisor.run()
        self.assertEqual('catalogue_refresh_complete', result['status'])
        commands = self.docker.calls
        creates = [c for c in commands if c[:2] == ['container', 'create']]
        self.assertEqual(2, len(creates))
        for command in creates:
            self.assertIn('--read-only', command)
            self.assertIn('--memory-swap', command)
            self.assertNotIn('--rm', command)
            self.assertEqual(1, command.count('--mount'))
            self.assertNotIn('--publish', command)
        self.assertTrue(all('--attach' not in c for c in commands))
        self.assertEqual('complete', self.supervisor.state['status'])
        self.assertFalse(self.docker.objects)
        marker = Path(self.supervisor.state['artifact_directory']) / 'host-complete.json'
        self.assertEqual('complete', json.loads(marker.read_text())['status'])

    def test_create_ack_lost_cleanup_recovers_cid_from_durable_intent(self):
        self.supervisor.new_run()
        self.docker.failure = 'create_ack_lost'
        with self.assertRaisesRegex(host.HostRejected, 'command_unavailable'):
            self.supervisor.worker('prepare')
        self.assertIsNone(self.supervisor.state['operation']['container_id'])
        self.assertEqual(1, len(self.docker.objects))
        self.supervisor.cleanup()
        self.assertFalse(self.docker.objects)
        self.assertIsNotNone(self.supervisor.state['operation']['container_id'])
        self.assertFalse(any(c[:2] == ['container', 'start'] for c in self.docker.calls))

    def test_lost_start_ack_cleanup_stops_only_verified_cid(self):
        self.supervisor.new_run()
        self.docker.failure = 'start_ack_lost'
        with self.assertRaises(host.HostRejected):
            self.supervisor.worker('prepare')
        cid = self.supervisor.state['operation']['container_id']
        self.supervisor.cleanup()
        self.assertIn(['container', 'stop', '--time', '30', cid], self.docker.calls)
        self.assertIn(['container', 'rm', cid], self.docker.calls)

    def test_scheduled_restart_never_replays_uncertain_publication(self):
        self.start_prepared()
        self.supervisor.save(status='publishing', publication_deadline=8200)
        before = len(self.docker.calls)
        with self.assertRaisesRegex(host.HostRejected, 'manual_review'):
            self.supervisor.run()
        self.assertEqual(before, len(self.docker.calls))

    def test_saved_configuration_change_fails_closed(self):
        self.supervisor.new_run()
        self.supervisor.config = {**self.config, 'config_sha256': '6' * 64}
        with self.assertRaisesRegex(host.HostRejected, 'saved_config_binding'):
            self.supervisor.load()

    def test_resume_does_not_reset_original_deadline(self):
        self.start_prepared()
        self.supervisor.save(status='requires_review', publication_deadline=900)
        with self.assertRaisesRegex(host.HostRejected, 'persisted_deadline_expired'):
            self.supervisor.run('resume', run_id=self.supervisor.state['run_id'])
        self.assertEqual(900, self.supervisor.state['publication_deadline'])

    def test_lost_start_never_allows_host_only_abandon_shortcut(self):
        self.start_prepared()
        self.docker.failure = 'start_ack_lost'
        with self.assertRaises(host.HostRejected):
            self.supervisor.worker('run')
        self.assertIsNotNone(self.supervisor.state['publication_start_intent'])
        self.docker.failure = None
        with patch.object(self.supervisor, 'worker', return_value={'status': 'worker_abandon'}) as worker:
            result = self.supervisor.run('abandon', run_id=self.supervisor.state['run_id'],
                reason_code='operator_reviewed')
        self.assertEqual('worker_abandon', result['status'])
        worker.assert_called_once_with('abandon', 'operator_reviewed')

    def test_failed_readonly_prepare_can_be_explicitly_abandoned(self):
        self.supervisor.new_run()
        self.docker.failure = 'create_ack_lost'
        with self.assertRaises(host.HostRejected):
            self.supervisor.worker('prepare')
        result = self.supervisor.run('abandon', run_id=self.supervisor.state['run_id'],
            reason_code='download_failed')
        self.assertEqual('catalogue_refresh_abandoned_before_publication', result['status'])
        self.assertEqual('abandoned', self.supervisor.state['status'])
        self.assertFalse(self.docker.objects)

    def test_deadline_crossed_during_logs_cannot_claim_success(self):
        self.supervisor.new_run()
        original = self.docker.call
        def crossing(arguments, **kwargs):
            result = original(arguments, **kwargs)
            if arguments[:2] == ['container', 'logs']:
                self.clock_value = self.supervisor.state['prepare_deadline']
            return result
        with patch.object(self.docker, 'call', side_effect=crossing):
            with self.assertRaisesRegex(host.HostRejected, 'phase_deadline'):
                self.supervisor.worker('prepare')
        self.assertIsNone(self.supervisor.state['prepared_sha256'])

    def test_image_and_network_pin_mutations_fail_before_create(self):
        original = self.docker.inspect
        mutations = [
            ('image', lambda o: o.update(Id='sha256:' + 'b' * 64)),
            ('image', lambda o: o['Config']['Labels'].update({'org.modish.release': 'build16'})),
            ('image', lambda o: o['Config']['Labels'].update({'org.modish.schema': '20261015_0028'})),
            ('image', lambda o: o['Config']['Labels'].update({'org.modish.release_run': 'invalid'})),
            ('image', lambda o: o['Config'].update(Volumes={'/unknown': {}})),
            ('network', lambda o: o.update(Id='b' * 64)),
            ('network', lambda o: o.update(Name='different_default')),
        ]
        for kind, mutate in mutations:
            with self.subTest(kind=kind, mutation=mutate):
                self.supervisor.new_run()
                def modified(actual_kind, identity, **kwargs):
                    result = original(actual_kind, identity, **kwargs)
                    if actual_kind == kind:
                        mutate(result)
                    return result
                with patch.object(self.docker, 'inspect', side_effect=modified):
                    with self.assertRaises(host.HostRejected):
                        self.supervisor.worker('prepare')
                self.assertFalse(self.docker.calls)

    def test_result_wrong_uuid_cannot_claim_publication_complete(self):
        self.start_prepared()
        self.docker.result_override = {'run_id': uuid4().hex}
        with self.assertRaisesRegex(host.HostRejected, 'result_binding'):
            self.supervisor.worker('run')
        self.assertNotEqual('complete', self.supervisor.state['status'])
        self.supervisor.cleanup()

    def test_prepare_cannot_choose_host_directory_or_forge_hash(self):
        for override in ({'directory': '/etc'}, {'prepared_sha256': 'a' * 64}, {'database_writes': True}):
            with self.subTest(override=override):
                self.supervisor.new_run()
                self.docker.result_override = override
                with self.assertRaises(host.HostRejected):
                    self.supervisor.worker('prepare')
                self.supervisor.cleanup()
                self.assertIsNone(self.supervisor.state['prepared_sha256'])

    def test_ownership_mutations_never_start_or_cleanup_a_container(self):
        mutations = [
            lambda o: o.update(Image='sha256:' + 'b' * 64),
            lambda o: o['Config']['Labels'].update({host.LABEL_PREFIX + 'run': uuid4().hex}),
            lambda o: o['Config'].update(Cmd=['unexpected']),
            lambda o: o['Mounts'][0].update(Source='/etc'),
            lambda o: o['Mounts'].append({'Type': 'volume'}),
            lambda o: o['HostConfig'].update(Privileged=True),
            lambda o: o['HostConfig'].update(ReadonlyRootfs=False),
            lambda o: o['HostConfig'].update(MemorySwap=-1),
            lambda o: o['HostConfig'].update(PidMode='host'),
            lambda o: o['HostConfig'].update(IpcMode='host'),
            lambda o: o['HostConfig'].update(Tmpfs={'/tmp': 'rw'}),
            lambda o: o['HostConfig'].update(PortBindings={'8000/tcp': [{'HostPort': '8000'}]}),
            lambda o: o['NetworkSettings']['Networks']['modish_default'].update(NetworkID='b' * 64),
            lambda o: o['Config']['Env'].append('PGHOST=elsewhere'),
            lambda o: o['Config'].update(StopSignal='SIGKILL'),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.supervisor.new_run()
                self.docker.inspection_mutation = mutation
                before = len(self.docker.calls)
                with self.assertRaises(host.HostRejected):
                    self.supervisor.worker('prepare')
                with self.assertRaises(host.HostRejected):
                    self.supervisor.cleanup()
                later = self.docker.calls[before:]
                self.assertFalse(any(c[:2] in (['container', 'start'], ['container', 'stop'],
                    ['container', 'rm']) for c in later))
                self.docker.inspection_mutation = None
                self.supervisor.cleanup()

    def test_unallocated_network_id_only_allowed_before_start(self):
        self.supervisor.new_run()
        self.docker.failure = 'create_ack_lost'
        with self.assertRaises(host.HostRejected):
            self.supervisor.worker('prepare')
        obj = next(iter(self.docker.objects.values()))
        obj['NetworkSettings']['Networks']['modish_default']['NetworkID'] = ''
        self.supervisor.verify_container(obj, self.supervisor.state['operation'])
        obj['State'].update(Status='running', Running=True)
        with self.assertRaisesRegex(host.HostRejected, 'network'):
            self.supervisor.verify_container(obj, self.supervisor.state['operation'])

    def test_runtime_pins_and_environment_are_checked_before_create(self):
        self.supervisor.new_run()
        self.env.write_bytes(ENV.replace(b'modish_postgres', b'other_postgres'))
        with self.assertRaisesRegex(host.HostRejected, 'database_binding'):
            self.supervisor.worker('prepare')
        self.assertFalse(self.docker.calls)

    def test_config_requires_exact_version_pins_root_and_env_hash(self):
        config_file = self.root / 'config.json'
        plain = {k: v for k, v in self.config.items() if k != 'config_sha256'}
        variants = [plain, {**plain, 'version': True}, {**plain, 'image_id': 'modish:latest'},
            {**plain, 'image_commit': 'HEAD'}, {**plain, 'network_name': 'bridge'},
            {**plain, 'workroot': str(self.root / 'elsewhere')}, {**plain, 'env_sha256': 'a' * 64},
            {**plain, 'unknown': 'rejected'}]
        with patch.object(host, 'WORKROOT', self.root):
            for value in variants:
                with self.subTest(value=value):
                    config_file.write_bytes(host.encoded(value))
                    if value is plain:
                        loaded = host.load_config(config_file)
                        self.assertEqual(host.digest(config_file.read_bytes()), loaded['config_sha256'])
                    else:
                        with self.assertRaises(host.HostRejected):
                            host.load_config(config_file)

    def test_retention_preserves_unknown_pending_and_current_artifacts(self):
        # Run six successful jobs; only five completed trees should remain.
        patch.object(host.Supervisor, 'verify_retention_tree').start()
        for number in range(7):
            self.clock_value = 1000 + number
            self.supervisor.run()
        completed = [p for p in self.root.glob('run-*') if (p / 'host-complete.json').exists()]
        self.assertEqual(5, len(completed))
        unknown = self.root / ('run-' + uuid4().hex)
        unknown.mkdir()
        pending = self.root / ('run-' + uuid4().hex)
        pending.mkdir()
        (pending / 'host-complete.json').write_bytes(host.encoded({'status': 'complete'}))
        self.supervisor.prune_complete()
        self.assertTrue(unknown.exists())
        self.assertTrue(pending.exists())

    def test_invalid_database_credentials_are_never_echoed(self):
        for raw in (ENV + b'PGHOST=hidden\n', ENV.replace(b'/modish', b'/modish?password=hidden'),
                ENV + b'DATABASE_URL=hidden\n'):
            with self.assertRaises(host.HostRejected) as raised:
                host.environment(raw)
            self.assertNotIn('hidden', str(raised.exception))


class DockerInvocationTests(unittest.TestCase):
    def test_fixed_local_daemon_scrubbed_environment_and_no_shell(self):
        def fake_run(command, **kwargs):
            self.assertEqual(['/usr/bin/docker', '--host', 'unix:///var/run/docker.sock'], command[:3])
            self.assertFalse(kwargs['shell'])
            self.assertNotIn('DOCKER_HOST', kwargs['env'])
            self.assertNotIn('DOCKER_CONFIG', kwargs['env'])
            kwargs['stdout'].write(b'{}')
            return type('Result', (), {'returncode': 0})()
        with patch.object(host.subprocess, 'run', side_effect=fake_run):
            self.assertEqual(b'{}', host.Docker().call(['version']))

    def test_daemon_failure_is_not_treated_as_absent_container(self):
        def fake_run(_command, **kwargs):
            kwargs['stderr'].write(b'Cannot connect to Docker daemon secret=hidden')
            return type('Result', (), {'returncode': 1})()
        with patch.object(host.subprocess, 'run', side_effect=fake_run):
            with self.assertRaisesRegex(host.HostRejected, '^host_docker_command_failed$'):
                host.Docker().inspect('container', 'a' * 64, absent_ok=True)

    def test_oversized_output_is_rejected(self):
        def fake_run(_command, **kwargs):
            kwargs['stdout'].write(b'a' * (host.MAX_JSON + 1))
            return type('Result', (), {'returncode': 0})()
        with patch.object(host.subprocess, 'run', side_effect=fake_run):
            with self.assertRaisesRegex(host.HostRejected, 'output_bound'):
                host.Docker().call(['container', 'logs', 'a' * 64])


class FilesystemGuardTests(unittest.TestCase):
    def test_duplicate_and_nonfinite_json_are_rejected(self):
        for raw in (b'{"version":1,"version":2}', b'{"number":NaN}', b'{"number":Infinity}'):
            with self.subTest(raw=raw), self.assertRaises(host.HostRejected):
                host.parse_json(raw)

    def test_directory_requires_exact_mode0700(self):
        for mode in (stat.S_IFDIR | 0o700, stat.S_IFDIR | 0o750, stat.S_IFREG | 0o600):
            fake_path = unittest.mock.MagicMock()
            fake_path.stat.return_value = SimpleNamespace(st_mode=mode)
            with patch.object(host, 'canonical', return_value=fake_path):
                if mode == stat.S_IFDIR | 0o700:
                    self.assertIs(fake_path, host.private_directory('/state'))
                else:
                    with self.assertRaisesRegex(host.HostRejected, 'private_directory'):
                        host.private_directory('/state')

    def test_private_file_rejects_permissions_owner_hardlink_and_oversize(self):
        good = {'st_mode': stat.S_IFREG | 0o600, 'st_uid': 0, 'st_nlink': 1, 'st_size': 2}
        variants = [good, {**good, 'st_mode': stat.S_IFREG | 0o640}, {**good, 'st_uid': 1000},
            {**good, 'st_nlink': 2}, {**good, 'st_size': host.MAX_JSON + 1}]
        for facts in variants:
            with self.subTest(facts=facts), tempfile.TemporaryDirectory() as folder:
                file = Path(folder) / 'private.json'
                file.write_bytes(b'{}')
                with patch.object(host, 'canonical', return_value=file), \
                        patch.object(host.os, 'fstat', return_value=SimpleNamespace(**facts)):
                    if facts == good:
                        self.assertEqual(b'{}', host.private_bytes(file))
                    else:
                        with self.assertRaisesRegex(host.HostRejected, 'private_bounded_file'):
                            host.private_bytes(file)

    def test_canonical_rejects_symlink_or_writable_parent(self):
        fake = unittest.mock.MagicMock()
        fake.is_absolute.return_value = True
        fake.resolve.return_value = fake
        fake.parents = []
        for mode, owner in ((stat.S_IFLNK | 0o777, 0), (stat.S_IFDIR | 0o777, 0),
                (stat.S_IFDIR | 0o755, 1000)):
            fake.lstat.return_value = SimpleNamespace(st_mode=mode, st_uid=owner)
            with patch.object(host, 'Path', return_value=fake):
                with self.assertRaisesRegex(host.HostRejected, 'root_owned_path'):
                    host.canonical('/private')

    def test_retention_tree_rejects_symlink_hardlink_or_public_file(self):
        good = {'st_mode': stat.S_IFREG | 0o600, 'st_uid': 0, 'st_nlink': 1}
        for facts in ({**good, 'st_mode': stat.S_IFLNK | 0o777}, {**good, 'st_nlink': 2},
                {**good, 'st_mode': stat.S_IFREG | 0o644}):
            with self.subTest(facts=facts), patch.object(host.os, 'walk', return_value=[('/state', [], ['entry'])]), \
                    patch.object(host.Path, 'lstat', return_value=SimpleNamespace(**facts)):
                with self.assertRaisesRegex(host.HostRejected, 'retention_owned_tree'):
                    host.Supervisor.verify_retention_tree(Path('/state'))


if __name__ == '__main__':
    unittest.main()
