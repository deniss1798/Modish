"""Pinned, single-run Linux host supervisor for the refresh17 worker.

Install this file root-owned with the example systemd units only after schema0029
and a sealed build17 image are deployed. No installation or migration happens
here. A private version-1 JSON config supplies image_id (sha256:...), image_commit,
image_source_sha256, network_name, network_id, env_file, env_sha256 and workroot.
The only workroot is /var/lib/modish/catalog-refresh17. Config/env files must be
canonical root-owned mode 0600 files; create workroot root-owned mode 0700 first.

An interrupted publication always requires explicit --phase status/resume/abandon
with --run-id. Resume keeps the original publication deadline; abandon resolves
pending SQL intent through the worker before closing it. Never clear current-run
by hand to retry an uncertain publication. Logs contain bounded reason codes.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from urllib.parse import unquote, urlsplit
from uuid import UUID, uuid4

DOCKER = '/usr/bin/docker'
WORKROOT = Path('/var/lib/modish/catalog-refresh17')
SCHEMA = '20261028_0029'
PREPARE_SECONDS = 1800
PUBLISH_SECONDS = 7200
MAX_JSON = 131072
RETAIN_COMPLETE = 5
HEX64 = re.compile(r'[0-9a-f]{64}')
LABEL_PREFIX = 'org.modish.catalog_refresh17.'


class HostRejected(RuntimeError):
    """A bounded operational code, never Docker output or credentials."""


def require(value, code):
    if not value:
        raise HostRejected(code)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def uuid_hex(value):
    try:
        parsed = UUID(value)
        require(parsed.version == 4 and parsed.hex == value, 'host_uuid4_hex_required')
    except (ValueError, TypeError, AttributeError):
        raise HostRejected('host_uuid4_hex_required') from None
    return value


def same_uuid(value, expected):
    try:
        return UUID(value) == UUID(expected)
    except (ValueError, TypeError, AttributeError):
        return False


def canonical(path):
    path = Path(path)
    require(path.is_absolute() and path == path.resolve(), 'host_canonical_path_required')
    # Parent ownership prevents a rename/symlink race by an unprivileged account.
    for parent in (path, *path.parents):
        info = parent.lstat()
        require(not stat.S_ISLNK(info.st_mode) and info.st_uid == 0
            and not info.st_mode & 0o022, 'host_root_owned_path_required')
    return path


def private_directory(path):
    path = canonical(path)
    info = path.stat()
    require(stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o700,
        'host_private_directory_required')
    return path


def private_bytes(path, *, maximum=MAX_JSON):
    path = canonical(path)
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
            and stat.S_IMODE(info.st_mode) == 0o600 and 0 < info.st_size <= maximum,
            'host_private_bounded_file_required')
        data = stream.read(maximum + 1)
    require(len(data) <= maximum, 'host_file_byte_bound')
    return data


def parse_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'host_duplicate_json_key')
            result[key] = value
        return result
    try:
        return json.loads(data, object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(HostRejected('host_nonfinite_json')))
    except (ValueError, UnicodeError):
        raise HostRejected('host_valid_json_required') from None


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def save_private(path, value):
    path = Path(path)
    private_directory(path.parent)
    require(not path.is_symlink(), 'host_state_symlink_forbidden')
    temporary = path.with_name('.' + path.name + '.' + uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(encoded(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def environment(data):
    require(len(data) <= 65536, 'host_env_byte_bound')
    try:
        lines = data.decode('utf-8').splitlines()
    except UnicodeError:
        raise HostRejected('host_env_encoding_required') from None
    result = {}
    for line in lines:
        if not line or line.startswith('#'):
            continue
        key, separator, value = line.partition('=')
        require(separator and re.fullmatch('[A-Za-z_][A-Za-z0-9_]*', key)
            and key not in result and all(32 <= ord(c) < 127 for c in value), 'host_env_format_required')
        require(not key.upper().startswith('PG') and key != 'AUDIT_FIXED_NOW_UTC',
            'host_forbidden_runtime_environment')
        result[key] = value
    require(result.get('APP_ENV', 'production') == 'production'
        and result.get('PYTHON_DOTENV_DISABLED', '1') == '1', 'host_production_environment_required')
    raw = result.get('DATABASE_URL', '')
    try:
        value = urlsplit(raw)
        valid = (value.scheme in {'postgresql', 'postgresql+psycopg'} and value.port in (None, 5432)
            and value.hostname == 'modish_postgres' and value.path == '/modish'
            and unquote(value.username or '') == 'modish_user' and value.password
            and not value.query and not value.fragment and '?' not in raw and '#' not in raw
            and '\\' not in raw and all(32 < ord(c) < 127 for c in raw))
    except ValueError:
        valid = False
    require(valid, 'host_production_database_binding_required')
    return {**result, 'APP_ENV': 'production', 'PYTHON_DOTENV_DISABLED': '1'}


def load_config(path):
    raw = private_bytes(path)
    value = parse_json(raw)
    keys = {'version', 'image_id', 'image_commit', 'image_source_sha256', 'network_name',
        'network_id', 'env_file', 'env_sha256', 'workroot'}
    require(isinstance(value, dict) and set(value) == keys and type(value['version']) is int
        and value['version'] == 1, 'host_config_version1_required')
    require(isinstance(value['image_id'], str) and re.fullmatch('sha256:[0-9a-f]{64}', value['image_id'])
        and isinstance(value['image_commit'], str) and re.fullmatch('[0-9a-f]{40}', value['image_commit'])
        and all(isinstance(value[key], str) and HEX64.fullmatch(value[key])
            for key in ('image_source_sha256', 'network_id', 'env_sha256')),
        'host_immutable_pins_required')
    require(value['network_name'] == 'modish_default' and value['workroot'] == str(WORKROOT),
        'host_fixed_runtime_binding_required')
    private_directory(value['workroot'])
    env = private_bytes(value['env_file'], maximum=65536)
    require(digest(env) == value['env_sha256'], 'host_env_hash_mismatch')
    environment(env)
    return {**value, 'config_sha256': digest(raw)}


class Docker:
    """Fixed local daemon, no shell, bounded private subprocess capture."""
    def call(self, arguments, *, timeout=30, absent_ok=False):
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            try:
                result = subprocess.run([DOCKER, '--host', 'unix:///var/run/docker.sock', *arguments],
                    stdin=subprocess.DEVNULL, stdout=output, stderr=errors, timeout=timeout,
                    check=False, shell=False, close_fds=True,
                    env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/root', 'LANG': 'C.UTF-8'})
            except (OSError, subprocess.TimeoutExpired):
                raise HostRejected('host_docker_command_unavailable') from None
            output.seek(0)
            raw = output.read(MAX_JSON + 1)
            require(len(raw) <= MAX_JSON, 'host_docker_output_bound')
            if result.returncode:
                errors.seek(0)
                error = errors.read(8192)
                # An absent object is different from a broken/unreachable daemon.
                if absent_ok and result.returncode == 1 and (
                        b'No such container:' in error or b'No such object:' in error):
                    return None
                raise HostRejected('host_docker_command_failed')
            return raw

    def inspect(self, kind, identity, *, absent_ok=False, timeout=30):
        raw = self.call([kind, 'inspect', identity], absent_ok=absent_ok, timeout=timeout)
        if raw is None:
            return None
        value = parse_json(raw)
        require(isinstance(value, list) and len(value) == 1 and isinstance(value[0], dict),
            'host_single_docker_object_required')
        return value[0]


def verify_runtime(config, docker, *, timeout=lambda: 30):
    image = docker.inspect('image', config['image_id'], timeout=timeout())
    labels = image.get('Config', {}).get('Labels') or {}
    require(image.get('Id') == config['image_id'] and labels.get('org.modish.release') == 'build17'
        and labels.get('org.modish.source_commit') == config['image_commit']
        and labels.get('org.modish.source_sha256') == config['image_source_sha256']
        and labels.get('org.modish.schema') == SCHEMA, 'host_sealed_build17_image_required')
    try:
        require(UUID(labels.get('org.modish.release_run', '')).version == 4,
            'host_image_release_run_required')
    except (ValueError, TypeError, AttributeError):
        raise HostRejected('host_image_release_run_required') from None
    require(not image.get('Config', {}).get('Volumes'), 'host_image_volumes_forbidden')
    network = docker.inspect('network', config['network_id'], timeout=timeout())
    require(network.get('Id') == config['network_id'] and network.get('Name') == config['network_name']
        and network.get('Driver') == 'bridge' and network.get('Scope') == 'local'
        and not network.get('Ingress'), 'host_pinned_network_required')
    env = environment(private_bytes(config['env_file'], maximum=65536))
    require(digest(private_bytes(config['env_file'], maximum=65536)) == config['env_sha256'],
        'host_env_hash_mismatch')
    merged = {}
    for item in image.get('Config', {}).get('Env') or []:
        key, separator, value = item.partition('=')
        require(separator and key not in merged and not key.upper().startswith('PG')
            and key != 'AUDIT_FIXED_NOW_UTC', 'host_image_environment_required')
        merged[key] = value
    merged.update(env)
    return digest(encoded(sorted(merged.items())))


@contextmanager
def exclusive_lock(root):
    import fcntl
    path = root / '.lock'
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
            and stat.S_IMODE(info.st_mode) == 0o600, 'host_private_lock_required')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise HostRejected('host_refresh_already_active') from None
        yield
    finally:
        os.close(fd)


def now():
    return time.time()


class Supervisor:
    def __init__(self, config, docker=None, *, clock=now, sleep=time.sleep):
        self.config, self.docker = config, docker or Docker()
        self.root = private_directory(config['workroot'])
        self.path = self.root / 'current-run.json'
        self.clock, self.sleep = clock, sleep
        self.state = None

    def save(self, **changes):
        self.state.update(changes, updated_at=self.clock())
        save_private(self.path, self.state)

    def load(self):
        if not self.path.exists():
            return None
        value = parse_json(private_bytes(self.path))
        require(isinstance(value, dict) and value.get('version') == 1
            and value.get('config_sha256') == self.config['config_sha256'], 'host_saved_config_binding_required')
        uuid_hex(value.get('run_id'))
        require(value.get('artifact_directory') == str(self.root / ('run-' + value['run_id'])),
            'host_saved_directory_binding_required')
        private_directory(value['artifact_directory'])
        self.state = value
        return value

    def new_run(self):
        run_id = uuid4().hex
        folder = self.root / ('run-' + run_id)
        folder.mkdir(mode=0o700)
        sync_directory(self.root)
        self.state = {'version': 1, 'run_id': run_id, 'config_sha256': self.config['config_sha256'],
            'artifact_directory': str(folder), 'status': 'preparing', 'operation': None,
            'prepared_sha256': None, 'publication_deadline': None, 'publication_start_intent': None,
            'prepare_deadline': self.clock() + PREPARE_SECONDS, 'created_at': self.clock()}
        self.save()

    def command(self, phase, reason_code=None):
        run_id = self.state['run_id']
        command = ['-B', '/app/scripts/catalog_refresh17.py', phase, '--directory',
            '/refresh' if phase == 'prepare' else '/refresh/run-' + run_id,
            '--mode', 'production', '--run-id', run_id]
        if phase != 'prepare':
            require(isinstance(self.state['prepared_sha256'], str)
                and HEX64.fullmatch(self.state['prepared_sha256']), 'host_prepared_hash_required')
            command += ['--prepared-sha256', self.state['prepared_sha256']]
        if phase == 'abandon':
            require(isinstance(reason_code, str) and re.fullmatch('[a-z][a-z0-9_]{0,63}', reason_code),
                'host_abandon_reason_required')
            command += ['--reason-code', reason_code]
        return command

    def labels(self, operation):
        return {LABEL_PREFIX + 'owner': 'host-v1', LABEL_PREFIX + 'run': self.state['run_id'],
            LABEL_PREFIX + 'operation': operation['id'], LABEL_PREFIX + 'phase': operation['phase'],
            LABEL_PREFIX + 'config': self.config['config_sha256']}

    def create_arguments(self, operation):
        args = ['container', 'create', '--name', operation['name'], '--read-only', '--cpus', '1',
            '--memory', '1536m', '--memory-swap', '1536m', '--pids-limit', '128', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges:true', '--network', self.config['network_id'],
            '--env-file', self.config['env_file'], '--env', 'APP_ENV=production',
            '--env', 'PYTHON_DOTENV_DISABLED=1', '--user', '0:0', '--workdir', '/app',
            '--restart', 'no', '--init', '--stop-signal', 'SIGTERM', '--stop-timeout', '30',
            '--log-driver', 'local', '--log-opt', 'max-size=1m', '--log-opt', 'max-file=1',
            '--mount', 'type=bind,src=' + self.state['artifact_directory'] + ',dst=/refresh',
            '--entrypoint', 'python']
        for key, value in sorted(self.labels(operation).items()):
            args += ['--label', key + '=' + value]
        return [*args, self.config['image_id'], *operation['command']]

    def verify_container(self, obj, operation):
        """This full binding is required before start, stop, kill, or removal."""
        config, host = obj.get('Config', {}), obj.get('HostConfig', {})
        cid = obj.get('Id')
        require(isinstance(cid, str) and HEX64.fullmatch(cid)
            and operation.get('container_id') in (None, cid)
            and obj.get('Name') == '/' + operation['name'] and obj.get('Image') == self.config['image_id']
            and config.get('Image') == self.config['image_id']
            and all((config.get('Labels') or {}).get(k) == v for k, v in self.labels(operation).items())
            and config.get('Entrypoint') == ['python'] and config.get('Cmd') == operation['command']
            and config.get('User') == '0:0' and config.get('WorkingDir') == '/app'
            and config.get('StopSignal') == 'SIGTERM', 'host_exact_owned_container_required')
        values = {}
        for entry in config.get('Env') or []:
            key, separator, value = entry.partition('=')
            require(separator and key not in values, 'host_container_environment_required')
            values[key] = value
        require(digest(encoded(sorted(values.items()))) == operation['environment_sha256'],
            'host_container_environment_required')
        require(host.get('ReadonlyRootfs') is True and host.get('NanoCpus') == 1000000000
            and host.get('Memory') == 1536 * 1024**2 and host.get('MemorySwap') == 1536 * 1024**2
            and host.get('PidsLimit') == 128 and host.get('CapDrop') == ['ALL']
            and not host.get('CapAdd') and host.get('SecurityOpt') == ['no-new-privileges:true']
            and host.get('Privileged') is False and host.get('Init') is True
            and not host.get('Binds') and not host.get('Devices') and not host.get('DeviceRequests')
            and not host.get('VolumesFrom') and not host.get('PortBindings')
            and not host.get('PublishAllPorts')
            and host.get('PidMode', '') == '' and host.get('IpcMode', 'private') == 'private'
            and host.get('UTSMode', '') == '' and not host.get('Tmpfs')
            and host.get('NetworkMode') == self.config['network_id']
            and host.get('RestartPolicy', {}).get('Name') == 'no'
            and host.get('AutoRemove') is False, 'host_container_isolation_required')
        mounts = obj.get('Mounts') or []
        require(len(mounts) == 1 and mounts[0].get('Type') == 'bind'
            and mounts[0].get('Source') == self.state['artifact_directory']
            and mounts[0].get('Destination') == '/refresh' and mounts[0].get('RW') is True
            and mounts[0].get('Propagation') == 'rprivate', 'host_exact_state_mount_required')
        networks = obj.get('NetworkSettings', {}).get('Networks') or {}
        network_id = next(iter(networks.values())).get('NetworkID') if networks else None
        unallocated = (obj.get('State', {}).get('Status') == 'created'
            and obj.get('State', {}).get('Running') is False and network_id == '')
        require(len(networks) == 1 and next(iter(networks)) == self.config['network_name']
            and (network_id == self.config['network_id'] or unallocated)
            and all(not bindings for bindings in (obj.get('NetworkSettings', {}).get('Ports') or {}).values()),
            'host_exact_container_network_required')
        return cid

    def owned_container(self, *, timeout=30):
        operation = self.state.get('operation')
        if operation is None:
            return None
        obj = self.docker.inspect('container', operation.get('container_id') or operation['name'],
            absent_ok=True, timeout=timeout)
        if obj is None:
            return None
        cid = self.verify_container(obj, operation)
        if operation.get('container_id') is None:
            operation['container_id'] = cid
            self.save(operation=operation)
        return obj

    def cleanup(self):
        obj = self.owned_container()
        if obj is None:
            return
        cid = obj['Id']
        if obj.get('State', {}).get('Running'):
            # Docker stop uses the verified SIGTERM and escalates only this CID.
            self.docker.call(['container', 'stop', '--time', '30', cid], timeout=45)
        obj = self.docker.inspect('container', cid, absent_ok=True)
        if obj is not None:
            self.verify_container(obj, self.state['operation'])
            require(not obj.get('State', {}).get('Running'), 'host_owned_container_still_running')
            self.docker.call(['container', 'rm', cid])

    def worker(self, phase, reason_code=None):
        require(not self.state.get('operation') or self.state['operation'].get('finished'),
            'host_unfinished_operation_requires_review')
        start = self.clock()
        if phase == 'run':
            require(self.state['publication_deadline'] is None, 'host_publication_already_attempted')
            self.save(publication_deadline=start + PUBLISH_SECONDS)
        deadline = (self.state['publication_deadline'] if phase in {'run', 'resume'} else
            self.state['prepare_deadline'] if phase == 'prepare' else start + PREPARE_SECONDS)
        require(isinstance(deadline, (int, float)) and start < deadline <= start + PUBLISH_SECONDS,
            'host_persisted_deadline_expired')
        def remaining(maximum=30):
            duration = deadline - self.clock()
            require(duration > 0, 'host_phase_deadline')
            return min(maximum, duration)
        environment_sha = verify_runtime(self.config, self.docker, timeout=remaining)
        remaining()
        operation_id = uuid4().hex
        operation = {'id': operation_id, 'name': 'modish-refresh17-' + self.state['run_id'] + '-' + operation_id,
            'phase': phase, 'command': self.command(phase, reason_code), 'container_id': None,
            'environment_sha256': environment_sha, 'create_intent': start, 'start_intent': None,
            'deadline': deadline, 'finished': False}
        # Intent survives loss of create acknowledgement; name can only be used
        # to discover a CID which then passes the complete ownership check.
        self.save(operation=operation)
        raw = self.docker.call(self.create_arguments(operation), timeout=remaining(60))
        cid = raw.decode('ascii').strip()
        require(HEX64.fullmatch(cid), 'host_full_container_id_required')
        operation['container_id'] = cid
        self.save(operation=operation)
        obj = self.owned_container(timeout=remaining())
        require(obj is not None and obj.get('State', {}).get('Status') == 'created'
            and not obj.get('State', {}).get('Running'), 'host_fresh_container_required')
        require(self.clock() < deadline, 'host_phase_deadline')
        operation['start_intent'] = self.clock()
        if phase in {'run', 'resume'} and self.state['publication_start_intent'] is None:
            self.save(publication_start_intent=operation['start_intent'])
        self.save(operation=operation)
        self.docker.call(['container', 'start', cid], timeout=remaining())
        while True:
            require(self.clock() < deadline, 'host_phase_deadline')
            obj = self.owned_container(timeout=remaining())
            remaining()
            require(obj is not None, 'host_started_container_missing_requires_review')
            state = obj.get('State', {})
            if not state.get('Running'):
                require(state.get('Status') == 'exited' and state.get('ExitCode') == 0
                    and state.get('OOMKilled') is False and not state.get('Error'),
                    'host_worker_failed_requires_review')
                break
            self.sleep(min(2, max(0, deadline - self.clock())))
        result = parse_json(self.docker.call(['container', 'logs', '--tail', '2', cid], timeout=remaining()))
        remaining()
        require(isinstance(result, dict) and same_uuid(result.get('run_id'), self.state['run_id']),
            'host_worker_result_binding_required')
        if phase == 'prepare':
            require(result.get('status') == 'catalogue_refresh_prepared'
                and result.get('directory') == '/refresh/run-' + self.state['run_id']
                and result.get('database_writes') is False, 'host_prepare_result_required')
            folder = private_directory(Path(self.state['artifact_directory']) / ('run-' + self.state['run_id']))
            raw = private_bytes(folder / 'prepared.json')
            prepared = parse_json(raw)
            require(result.get('prepared_sha256') == digest(raw)
                and prepared.get('run_id') == self.state['run_id'] and prepared.get('schema') == SCHEMA
                and prepared.get('schema_version') == 1
                and isinstance(prepared.get('snapshot_sha256'), str)
                and HEX64.fullmatch(prepared['snapshot_sha256'])
                and result.get('snapshot_sha256') == prepared['snapshot_sha256']
                and prepared.get('mode') == 'production' and prepared.get('status') == 'catalogue_refresh_prepared',
                'host_prepared_file_binding_required')
            self.save(prepared_sha256=digest(raw), status='prepared')
        elif phase in {'run', 'resume'}:
            require(result.get('status') == 'catalogue_refresh_complete', 'host_publication_not_complete')
            publication = parse_json(private_bytes(Path(self.state['artifact_directory']) /
                ('run-' + self.state['run_id']) / 'publication.json'))
            require(publication.get('status') == 'complete' and publication.get('pending') is None
                and same_uuid(publication.get('run_id'), self.state['run_id'])
                and publication.get('snapshot_sha256') == result.get('snapshot_sha256')
                and publication.get('snapshot_sha256') == parse_json(private_bytes(
                    Path(self.state['artifact_directory']) / ('run-' + self.state['run_id']) /
                    'prepared.json')).get('snapshot_sha256'), 'host_complete_checkpoint_required')
            self.save(status='complete')
        elif phase == 'abandon':
            require(result.get('status') in {'catalogue_refresh_interrupted',
                'catalogue_refresh_abandoned_before_initialization'}, 'host_abandon_result_required')
            self.save(status='abandoned')
        else:
            require(phase == 'status' and result.get('status') in {
                'prepared_not_published', 'running', 'complete', 'interrupted'}, 'host_status_result_required')
        operation['finished'] = True
        self.save(operation=operation)
        self.cleanup()
        if self.state['status'] in {'complete', 'abandoned'}:
            self.save(operation=None)
        if self.state['status'] == 'complete':
            self.complete_marker()
        return {'status': result['status'], 'run_id': self.state['run_id']}

    def complete_marker(self):
        marker = {'version': 1, 'run_id': self.state['run_id'], 'status': 'complete',
            'config_sha256': self.config['config_sha256'], 'prepared_sha256': self.state['prepared_sha256'],
            'completed_at': self.clock()}
        save_private(Path(self.state['artifact_directory']) / 'host-complete.json', marker)

    def prune_complete(self):
        """Only exact completed pinned UUID trees; pending/unknown evidence stays."""
        completed = []
        for folder in self.root.iterdir():
            if not re.fullmatch(r'run-[0-9a-f]{32}', folder.name) or folder.is_symlink():
                continue
            try:
                run_id = uuid_hex(folder.name[4:])
                private_directory(folder)
                marker = parse_json(private_bytes(folder / 'host-complete.json'))
                publication = parse_json(private_bytes(folder / ('run-' + run_id) / 'publication.json'))
                prepared = private_bytes(folder / ('run-' + run_id) / 'prepared.json')
                prepared_value = parse_json(prepared)
                require(marker.get('version') == 1 and marker.get('status') == 'complete'
                    and marker.get('run_id') == run_id and marker.get('config_sha256') == self.config['config_sha256']
                    and marker.get('prepared_sha256') == digest(prepared)
                    and publication.get('status') == 'complete' and publication.get('pending') is None
                    and isinstance(prepared_value.get('snapshot_sha256'), str)
                    and HEX64.fullmatch(prepared_value['snapshot_sha256'])
                    and publication.get('snapshot_sha256') == prepared_value['snapshot_sha256']
                    and same_uuid(publication.get('run_id'), run_id), 'host_retention_complete_binding_required')
                # No symlink, hardlink, ownership or permission surprises may be
                # traversed by recursive deletion, even inside a completed tree.
                self.verify_retention_tree(folder)
                completed.append((marker['completed_at'], folder))
            except (HostRejected, OSError, ValueError, KeyError, TypeError):
                continue
        for _, folder in sorted(completed, key=lambda item: item[0], reverse=True)[RETAIN_COMPLETE:]:
            if self.state and folder == Path(self.state['artifact_directory']):
                continue
            require(folder.parent == self.root and folder.resolve().parent == self.root,
                'host_retention_fixed_root_required')
            shutil.rmtree(folder)
            sync_directory(self.root)

    @staticmethod
    def verify_retention_tree(folder):
        for root, directories, files in os.walk(folder, followlinks=False):
            for name in (*directories, *files):
                info = (Path(root) / name).lstat()
                require(info.st_uid == 0 and not info.st_mode & 0o077
                    and (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode) and info.st_nlink == 1),
                    'host_retention_owned_tree_required')

    def run(self, phase='automatic', *, run_id=None, reason_code=None):
        self.load()
        if phase == 'automatic':
            require(self.state is None or self.state['status'] in {'complete', 'abandoned'},
                'host_unfinished_run_requires_manual_review')
            if self.state is not None:
                self.cleanup()
                self.save(operation=None)
            self.prune_complete()
            self.new_run()
            self.worker('prepare')
            self.save(status='publishing')
            result = self.worker('run')
            self.prune_complete()
            return result
        require(self.state is not None and uuid_hex(run_id) == self.state['run_id'],
            'host_manual_run_binding_required')
        # Explicit recovery never overlaps an old worker. Even status waits for
        # verified cleanup before opening a fresh, read-only worker container.
        self.cleanup()
        previous = self.state.get('operation')
        if previous and not previous.get('finished'):
            previous['finished'] = True
            self.save(operation=previous, status='requires_review')
        require(phase in {'status', 'resume', 'abandon'}, 'host_manual_phase_required')
        if phase == 'abandon' and self.state['publication_start_intent'] is None:
            require(isinstance(reason_code, str) and re.fullmatch('[a-z][a-z0-9_]{0,63}', reason_code),
                'host_abandon_reason_required')
            # No publication start was ever authorized durably. Preparation and
            # an unstarted Docker create cannot have written catalogue rows.
            self.save(status='abandoned', operation=None, abandon_reason=reason_code)
            return {'status': 'catalogue_refresh_abandoned_before_publication', 'run_id': run_id}
        return self.worker(phase, reason_code)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('/etc/modish/catalog-refresh17.json'))
    parser.add_argument('--cleanup', action='store_true')
    parser.add_argument('--phase', choices=('automatic', 'status', 'resume', 'abandon'), default='automatic')
    parser.add_argument('--run-id')
    parser.add_argument('--reason-code')
    args = parser.parse_args(argv)
    require(sys.platform == 'linux' and os.geteuid() == 0, 'host_linux_root_required')
    require((args.phase == 'abandon') == (args.reason_code is not None), 'host_phase_reason_required')
    require((args.phase != 'automatic') == (args.run_id is not None), 'host_phase_run_required')
    config = load_config(args.config)
    supervisor = Supervisor(config)
    def interrupted(_number, _frame):
        raise HostRejected('host_interrupted_requires_review')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    with exclusive_lock(supervisor.root):
        try:
            if args.cleanup:
                if supervisor.load() is not None:
                    supervisor.cleanup()
                return {'status': 'host_owned_container_cleanup_complete'}
            return supervisor.run(args.phase, run_id=args.run_id, reason_code=args.reason_code)
        except BaseException:
            if supervisor.state is not None:
                if supervisor.state.get('status') not in {'complete', 'abandoned'}:
                    supervisor.save(status='requires_review')
                supervisor.cleanup()
            raise


if __name__ == '__main__':
    try:
        print(json.dumps(main(), sort_keys=True, separators=(',', ':')))
    except BaseException as error:
        reason = str(error) if isinstance(error, HostRejected) else 'host_refresh_failed'
        if not re.fullmatch('[a-z][a-z0-9_]{0,95}', reason):
            reason = 'host_refresh_failed'
        print(json.dumps({'status': 'catalogue_refresh_requires_review', 'reason': reason,
            'automatic_database_restore': False, 'manual_recovery_required': True}))
        raise SystemExit(1)
