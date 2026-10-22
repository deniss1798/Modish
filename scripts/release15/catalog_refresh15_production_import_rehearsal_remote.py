"""Exercise the UNMODIFIED production importer on a NEW isolated PG server.

No production SQL, SSH, downloads, production stop/start, drop, rm or retry.
Production-like DB names exist only on a new internal network with alias postgres.
Private fixtures, server and outcome ledger remain for review. On worker timeout
only its exact owned container may be stopped; PostgreSQL/data are retained.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import shutil
from time import monotonic, sleep
from uuid import UUID

RUN = '9c4c8c7fe5a045068cde76d81df12104'
ARCHIVE = '5184f378245db0073154369f1b6e083c8d584cb9006788993cd4727866e26c24'
IMAGE = 'sha256:633bce227d3ce3277137c77f32f2c6e83bb338d91784803df424d7592a464aec'
IMPORTER_SHA = 'b9bf37d9bb911f6354e7182922aa1d4acad78bc14f7d9f2f9632a4773ebc0688'
BASE_SHA = 'e1eeb23e6be60e5de4230c07313f115434f2ef03974909182ef9db0249e9fc44'
CLONE_SHA = 'a91130f331c9a909116e15a7eb587afde5a27db2fdd04a996d60b54e549733f0'
MANIFEST = '5d2dde18bcb09cde948d044bc57d2178fd035aebc0698a0996d79dd5ab4ce23c'
DUMP_SHA = 'f057a02239d2da9fccb69020734bf461ca1d7f2c7cd8631864162cfc6f47e50e'
DUMP_BYTES = 201121527
APPROVAL = 'APPROVE_BUILD15_AND_FOUR_SOURCE_PRODUCTION_IMPORT'
JWT = 'unused-build15-catalog-import-worker-no-auth'
FEEDS = {'baon': '19982', 'shoppinglive': '25461', 'serginnetti': '26417', 'postmeridiem': '25200'}
ROWS = {'baon': 9318, 'shoppinglive': 43402, 'serginnetti': 17065, 'postmeridiem': 378}
R = Path('/opt/modish-releases/20261021-catalog-refresh15-' + RUN)
MIB = 1024 * 1024
BASE = None
S = None
ARGS = None
NETWORK = None
NETWORK_ID = None
PG = None
PG_ID = None
ADMIN = 'refresh15_admin'
PGDATA_PATH = '/var/lib/postgresql/data'
DEADLINE = None
PHASE = 'arguments'
COUNTER = 0
ACTIVE_WORKER = None
STAGE_CREATED = False


def require(value, code):
    if not value:
        raise RuntimeError(code)


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def regular(path, bound, expected=None):
    require(path.is_file() and not path.is_symlink() and path.stat().st_uid == 0
        and path.stat().st_mode & 0o777 == 0o600 and 0 < path.stat().st_size <= bound,
        'private_regular_input_required')
    if expected:
        require(sha(path) == expected, 'pinned_input_hash_required')


def private(path, raw):
    with path.open('xb') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def command(label, argv, **options):
    global COUNTER
    COUNTER += 1
    remaining = DEADLINE - monotonic()
    require(remaining > 0, 'exercise_total_timeout')
    options['timeout'] = min(options.get('timeout', 40), remaining)
    return BASE.run('exercise_' + str(COUNTER).zfill(4) + '_' + label, argv, **options)


def inspect(name):
    value = json.loads(command('inspect', [*BASE.DOCKER, 'inspect', name]))
    require(isinstance(value, list) and len(value) == 1, 'one_container_metadata_required')
    return value[0]


def network_guard():
    value = json.loads(command('network', [*BASE.DOCKER, 'network', 'inspect', NETWORK]))[0]
    require(value['Id'] == NETWORK_ID and value['Name'] == NETWORK and value.get('Internal') is True
        and value.get('Driver') == 'bridge'
        and value.get('Labels', {}).get('org.modish.production_import_exercise') == ARGS.exercise_id,
        'new_internal_exercise_network_required')
    allowed_ids = {PG_ID} if PG_ID else set()
    if ACTIVE_WORKER:
        allowed_ids.add(ACTIVE_WORKER[1])
    allowed_names = {'modish_prodproof_' + kind + '_' + ARGS.exercise_id for kind in ('pg', 'migrate', 'import')}
    require(all(identity in allowed_ids and item.get('Name') in allowed_names
        for identity, item in (value.get('Containers') or {}).items()), 'unexpected_network_member_requires_review')
    for name in BASE.PROD:
        require(NETWORK not in inspect(name)['NetworkSettings']['Networks'], 'production_must_be_off_exercise_network')


def container_guard(name, identity, image, kind, mounts, *, running=None):
    obj = inspect(identity)
    labels = obj['Config'].get('Labels') or {}
    require(obj['Id'] == identity and obj['Name'] == '/' + name and obj['Image'] == image
        and labels.get('org.modish.production_import_exercise') == ARGS.exercise_id
        and labels.get('org.modish.exercise_kind') == kind
        and set(obj['NetworkSettings']['Networks']) == {NETWORK}
        and obj['NetworkSettings']['Networks'][NETWORK]['NetworkID'] in (
            (NETWORK_ID,) if obj['State']['Running'] else ('', NETWORK_ID)),
        'owned_single_network_exercise_container_required')
    host = obj['HostConfig']
    require(not host.get('PortBindings') and not host.get('Privileged') and host.get('ReadonlyRootfs') is True
        and host.get('NetworkMode') == NETWORK and host.get('PidMode', '') == ''
        and host.get('IpcMode') != 'host' and not host.get('Devices')
        and host.get('RestartPolicy', {}).get('Name') == 'no'
        and any(value.startswith('no-new-privileges') for value in host.get('SecurityOpt', [])),
        'bounded_private_exercise_container_required')
    expected_memory = 512 * MIB if kind == 'postgres' else (768 * MIB if kind == 'migrate' else 1536 * MIB)
    expected_cpu = 500000000 if kind == 'postgres' else (750000000 if kind == 'migrate' else 1000000000)
    require(host.get('Memory') == expected_memory and host.get('MemorySwap') == expected_memory
        and host.get('NanoCpus') == expected_cpu and host.get('PidsLimit') == 128,
        'exact_exercise_resource_limits_required')
    caps = {value.removeprefix('CAP_') for value in host.get('CapAdd') or []}
    require('ALL' in (host.get('CapDrop') or []) and caps == (
        {'CHOWN', 'SETUID', 'SETGID', 'DAC_OVERRIDE', 'FOWNER'} if kind == 'postgres' else set()),
        'exact_exercise_capabilities_required')
    actual = {item['Destination']: (item['Source'], item['RW']) for item in obj['Mounts'] if item['Type'] == 'bind'}
    require(actual == mounts and all(item['Type'] in {'bind', 'tmpfs'} for item in obj['Mounts']),
        'exact_exercise_mounts_required')
    if running is not None:
        require(obj['State']['Running'] is running, 'expected_exercise_running_state_required')
    return obj


def pg_guard():
    obj = container_guard(PG, PG_ID, BASE.PROD['modish_postgres'][1], 'postgres',
        {'/var/lib/postgresql/data': (str(S / 'pgdata'), True)}, running=True)
    env = dict(value.split('=', 1) for value in obj['Config'].get('Env', []) if '=' in value)
    require(env.get('POSTGRES_USER') == ADMIN and env.get('POSTGRES_DB') == 'postgres'
        and env.get('PGDATA') == PGDATA_PATH
        and not BASE.PG_ENV.intersection(env)
        and 'postgres' in obj['NetworkSettings']['Networks'][NETWORK].get('Aliases', []),
        'isolated_server_admin_and_alias_required')


def sql(statement, *, database='modish', write=False):
    pg_guard()
    options = '-c statement_timeout=180000 -c lock_timeout=5000 -c timezone=UTC'
    if not write:
        options += ' -c default_transaction_read_only=on'
    return command('sql', [*BASE.DOCKER, 'exec', '-i', '-e', 'PGHOST=/var/run/postgresql',
        '-e', 'PGOPTIONS=' + options, PG_ID, 'timeout', '-k', '5s', '195s',
        'psql', '-X', '-qAt', '-U', ADMIN, '-d', database, '--set', 'ON_ERROR_STOP=1'],
        data=statement.encode(), timeout=210).decode().strip()


def full_integrity():
    tables = sql("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename;").splitlines()
    require(len(tables) == 31 and all(re.fullmatch(r'[a-z][a-z0-9_]{0,62}', name) for name in tables),
        'all_31_application_tables_required')
    primary = json.loads(sql("SELECT json_object_agg(tablename,columns) FROM (SELECT t.relname AS tablename, "
        "json_agg(a.attname ORDER BY k.position) AS columns FROM pg_index i JOIN pg_class t ON t.oid=i.indrelid "
        "JOIN pg_namespace n ON n.oid=t.relnamespace CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum,position) "
        "JOIN pg_attribute a ON a.attrelid=t.oid AND a.attnum=k.attnum WHERE i.indisprimary AND n.nspname='public' "
        "AND k.position<=i.indnkeyatts GROUP BY t.relname) p;"))
    require(set(primary) == set(tables), 'all_primary_keys_required')
    counts, hashes = {}, {}
    for table in tables:
        columns = primary[table]
        require(isinstance(columns, list) and 0 < len(columns) <= 16
            and all(re.fullmatch(r'[a-z][a-z0-9_]{0,62}', value) for value in columns), 'bounded_primary_keys_required')
        payload = 'to_jsonb(t)'
        if table == 'products':
            payload += " || jsonb_build_object('price',t.price::numeric(14,2),'old_price',t.old_price::numeric(14,2))"
        elif table == 'outfits':
            payload += " || jsonb_build_object('total_price',t.total_price::numeric(14,2))"
        elif table == 'users':
            payload += " - 'recommendation_revision'"
        elif table == 'alembic_version':
            payload += " - 'version_num'"
        order = ','.join('t."' + name + '"' for name in columns)
        value = json.loads(sql("SELECT json_build_object('count',count(*),'hash',md5(coalesce(string_agg(md5((" +
            payload + ")::text),'' ORDER BY " + order + "),''))) FROM public.\"" + table + '\" t;'))
        require(type(value.get('count')) is int and value['count'] >= 0
            and re.fullmatch(r'[0-9a-f]{32}', value.get('hash', '')), 'aggregate_row_proof_required')
        counts[table], hashes[table] = value['count'], value['hash']
    return counts, hashes


def base_worker_options(name, kind, memory, cpu):
    return [*BASE.DOCKER, 'create', '--name', name, '--network', NETWORK,
        '--label', 'org.modish.production_import_exercise=' + ARGS.exercise_id,
        '--label', 'org.modish.exercise_kind=' + kind, '--cpus=' + cpu,
        '--memory=' + memory, '--memory-swap=' + memory, '--pids-limit=128',
        '--read-only', '--security-opt=no-new-privileges', '--cap-drop=ALL',
        '--restart=no', '--tmpfs=/tmp:rw,nosuid,nodev,size=64m']


def stop_worker():
    global DEADLINE
    if ACTIVE_WORKER:
        # A separate bounded ownership check/stop remains possible if the main
        # deadline expired. This cannot start workers, touch SQL or remove data.
        previous_deadline = DEADLINE
        DEADLINE = max(DEADLINE, monotonic() + 45)
        try:
            name, identity, kind, mounts = ACTIVE_WORKER
            obj = container_guard(name, identity, IMAGE, kind, mounts)
            if obj['State']['Running']:
                command('stop_owned_worker', [*BASE.DOCKER, 'stop', '--time', '10', identity], timeout=20)
        finally:
            DEADLINE = previous_deadline


def worker(kind, envpath, mounts, tail, timeout):
    global ACTIVE_WORKER
    name = 'modish_prodproof_' + kind + '_' + ARGS.exercise_id
    options = base_worker_options(name, kind, '768m' if kind == 'migrate' else '1536m', '0.75' if kind == 'migrate' else '1')
    options += ['--env-file', str(envpath), '--entrypoint=python']
    for destination, (source, writable) in mounts.items():
        options += ['--mount', 'type=bind,src=' + source + ',dst=' + destination + ('' if writable else ',readonly')]
    identity = command('create_worker', [*options, IMAGE, *tail]).decode().strip()
    require(re.fullmatch(r'[0-9a-f]{64}', identity), 'created_immutable_worker_id_required')
    ACTIVE_WORKER = (name, identity, kind, mounts)
    container_guard(name, identity, IMAGE, kind, mounts, running=False)
    network_guard()
    try:
        raw = command('run_' + kind, [*BASE.DOCKER, 'start', '--attach', identity], timeout=timeout)
    except Exception:
        stop_worker()
        raise
    obj = container_guard(name, identity, IMAGE, kind, mounts, running=False)
    require(obj['State'].get('ExitCode') == 0 and obj['State'].get('OOMKilled') is False,
        'successful_bounded_worker_exit_required')
    ACTIVE_WORKER = None
    return raw


def snapshot_guard():
    feeds = R / 'feeds'
    require(feeds.is_dir() and not feeds.is_symlink() and feeds.resolve() == feeds
        and feeds.stat().st_uid == 0 and feeds.stat().st_mode & 0o777 == 0o700, 'private_canonical_feed_directory_required')
    regular(R / 'feeds' / 'download-manifest.json', 16384, MANIFEST)
    data = json.loads((R / 'feeds' / 'download-manifest.json').read_bytes())
    require(data.get('run_id') == RUN and data.get('result') == 'download_complete'
        and data.get('observation_clock_basis') == 'real_download_request_start_utc'
        and len(data.get('sources', [])) == 4 and {item['code'] for item in data['sources']} == set(FEEDS),
        'rehearsed_real_snapshots_required')
    now = datetime.now(timezone.utc)
    for entry in data['sources']:
        code = entry['code']
        require(entry['filename'] == code + '.csv' and entry['feed_id'] == FEEDS[code], 'fixed_snapshot_identity_required')
        regular(R / 'feeds' / entry['filename'], 256 * MIB, entry['sha256'])
        require((R / 'feeds' / entry['filename']).stat().st_size == entry['bytes'], 'snapshot_bytes_match_required')
        stamp = datetime.fromisoformat(entry['observed_at'])
        finish = datetime.fromisoformat(entry['download_finished_at'])
        require(stamp.tzinfo is not None and finish.tzinfo is not None
            and stamp.utcoffset().total_seconds() == finish.utcoffset().total_seconds() == 0
            and now - timedelta(hours=72) <= stamp <= finish <= now + timedelta(minutes=5), 'fresh_actual_snapshot_required')


def main():
    global BASE, S, ARGS, NETWORK, NETWORK_ID, PG, PG_ID, DEADLINE, PHASE, STAGE_CREATED
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exercise-id', required=True)
    parser.add_argument('--root', required=True)
    parser.add_argument('--importer-sha256', required=True)
    ARGS = args = parser.parse_args()
    require(os.name == 'posix' and os.geteuid() == 0, 'linux_root_required')
    require(re.fullmatch(r'[0-9a-f]{32}', args.exercise_id) and UUID(args.exercise_id).version == 4
        and args.exercise_id != RUN, 'new_uuid4_exercise_required')
    require(args.importer_sha256 == IMPORTER_SHA, 'reviewed_production_importer_required')
    require(R.is_dir() and not R.is_symlink() and R.resolve() == R
        and R.stat().st_uid == 0 and R.stat().st_mode & 0o777 == 0o700, 'private_source_stage_required')
    S = Path(args.root)
    require(S == R / ('production-import-exercise-' + args.exercise_id) and not S.exists()
        and not S.is_symlink(), 'new_fixed_exercise_stage_required')
    regular(R / 'catalog_refresh15_clone_remote.py', 131072, BASE_SHA)
    regular(R / 'catalog_refresh15_production_import.py', 131072, IMPORTER_SHA)
    regular(R / 'clone-report.json', 131072, CLONE_SHA)
    clone = json.loads((R / 'clone-report.json').read_bytes())
    require(clone.get('result') == 'clone_migrated_verified' and clone.get('run_id') == RUN
        and clone.get('image_id') == IMAGE and clone.get('archive_sha256') == ARCHIVE
        and clone.get('normalized_full_row_integrity_preserved') is True
        and len(clone.get('counts_before_migration', {})) == 31
        and clone.get('counts_before_migration') == clone.get('counts_after_migration')
        and clone.get('row_integrity_before_migration') == clone.get('row_integrity_after_migration'),
        'all_31_source_row_proofs_required')
    regular(R / 'production.dump', DUMP_BYTES, DUMP_SHA)
    require((R / 'production.dump').stat().st_size == DUMP_BYTES, 'immutable_dump_size_required')
    snapshot_guard()
    memory = Path('/proc/meminfo').read_text()
    match = re.search(r'^MemAvailable:\s+(\d+) kB$', memory, re.MULTILINE)
    require(match is not None and int(match.group(1)) * 1024 >= 2300 * MIB, 'minimum_2300_mib_available_required')
    require(shutil.disk_usage(R).free >= 8 * 1024**3, 'minimum_8_gib_disk_free_required')
    spec = importlib.util.spec_from_file_location('pinned_production_import_exercise_utilities', R / 'catalog_refresh15_clone_remote.py')
    BASE = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(BASE)
    os.umask(0o077)
    S.mkdir(mode=0o700)
    STAGE_CREATED = True
    BASE.R = S
    client = S / 'docker-client'
    client.mkdir(mode=0o700)
    BASE.json_file(client / 'config.json', {})
    BASE.DOCKER.extend(['--config', str(client)])
    DEADLINE = monotonic() + 10800
    BASE.json_file(S / 'started.json', {'scope': 'new_isolated_server_unmodified_production_importer',
        'exercise_id': args.exercise_id, 'run_id': RUN, 'production_sql_executed': False, 'automatic_retry': False})
    PHASE = 'metadata_preflight'
    baseline = BASE.production_snapshot('exercise_production_before')
    image = json.loads(command('candidate_image', [*BASE.DOCKER, 'image', 'inspect', IMAGE]))[0]
    BASE.require_image(image, IMAGE, RUN)
    pg_image = BASE.PROD['modish_postgres'][1]
    require(json.loads(command('postgres_image', [*BASE.DOCKER, 'image', 'inspect', pg_image]))[0]['Id'] == pg_image,
        'pinned_postgresql_image_required')
    NETWORK = 'modish-prodproof-' + args.exercise_id
    PG = 'modish_prodproof_pg_' + args.exercise_id
    require(not command('network_absent', [*BASE.DOCKER, 'network', 'ls', '--filter', 'name=^' + NETWORK + '$', '--format', '{{.ID}}']).strip(),
        'new_network_name_required')
    for kind in ('pg', 'migrate', 'import'):
        require(not command('container_absent', [*BASE.DOCKER, 'ps', '--all', '--filter', 'name=^/modish_prodproof_' + kind + '_' + args.exercise_id + '$',
            '--format', '{{.ID}}']).strip(), 'new_container_name_required')
    PHASE = 'new_isolated_server'
    NETWORK_ID = command('create_network', [*BASE.DOCKER, 'network', 'create', '--internal', '--driver=bridge',
        '--label', 'org.modish.production_import_exercise=' + args.exercise_id, NETWORK]).decode().strip()
    require(re.fullmatch(r'[0-9a-f]{64}', NETWORK_ID), 'immutable_new_network_id_required')
    network_guard()
    (S / 'pgdata').mkdir(mode=0o700)
    admin_password, worker_password = secrets.token_urlsafe(48), secrets.token_urlsafe(48)
    private(S / 'postgres.env.private', ('POSTGRES_USER=' + ADMIN + '\nPOSTGRES_DB=postgres\nPOSTGRES_PASSWORD=' + admin_password +
        '\nPGDATA=' + PGDATA_PATH + '\nPOSTGRES_INITDB_ARGS=--auth-host=scram-sha-256 --auth-local=trust\n').encode())
    admin_password = None
    options = base_worker_options(PG, 'postgres', '512m', '0.5')
    options += ['--network-alias=postgres', '--cap-add=CHOWN', '--cap-add=SETUID', '--cap-add=SETGID', '--cap-add=DAC_OVERRIDE', '--cap-add=FOWNER',
        '--tmpfs=/var/run/postgresql:rw,nosuid,nodev,mode=0775,size=16m', '--env-file', str(S / 'postgres.env.private'),
        '--mount', 'type=bind,src=' + str(S / 'pgdata') + ',dst=/var/lib/postgresql/data']
    PG_ID = command('create_postgres', [*options, pg_image, 'postgres', '-c', 'max_connections=15', '-c', 'shared_buffers=96MB',
        '-c', 'work_mem=4MB', '-c', 'maintenance_work_mem=64MB', '-c', 'autovacuum=off', '-c', 'max_worker_processes=2',
        '-c', 'max_parallel_workers=0', '-c', 'max_parallel_maintenance_workers=0']).decode().strip()
    require(re.fullmatch(r'[0-9a-f]{64}', PG_ID), 'immutable_new_postgres_id_required')
    container_guard(PG, PG_ID, pg_image, 'postgres', {'/var/lib/postgresql/data': (str(S / 'pgdata'), True)}, running=False)
    command('start_postgres', [*BASE.DOCKER, 'start', PG_ID])
    for attempt in range(30):
        pg_guard()
        try:
            command('postgres_ready', [*BASE.DOCKER, 'exec', PG_ID, 'timeout', '5s', 'pg_isready', '-U', ADMIN, '-d', 'postgres'], timeout=10)
            break
        except BASE.GuardError as error:
            require(str(error) == 'command_failed', 'postgres_startup_requires_review')
            sleep(1)
    else:
        raise RuntimeError('new_postgres_readiness_timeout')
    require(sql("SELECT count(*) FROM pg_database WHERE datname='modish';", database='postgres') == '0'
        and sql("SELECT count(*) FROM pg_roles WHERE rolname='modish_user';", database='postgres') == '0', 'strictly_new_modish_identity_required')
    sql("CREATE ROLE modish_user LOGIN PASSWORD '" + worker_password + "' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 10;",
        database='postgres', write=True)
    sql('CREATE DATABASE modish OWNER modish_user TEMPLATE template0;', database='postgres', write=True)
    sql('REVOKE ALL ON DATABASE modish FROM PUBLIC;', database='postgres', write=True)
    env = {'DATABASE_URL': 'postgresql+psycopg://modish_user:' + worker_password + '@postgres:5432/modish',
        'APP_ENV': 'production', 'JWT_SECRET': JWT, 'PYTHON_DOTENV_DISABLED': '1', 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONOPTIMIZE': '0',
        'ADMITAD_ENABLED': 'false', 'STYLE_VISUAL_ENABLED': 'false', 'CLOSED_ALPHA_ENABLED': 'false', 'ALLOW_DEV_ANALYZE': 'false',
        'REFRESH_RUN_ID': RUN, 'AUDIT_ARCHIVE_SHA256': ARCHIVE, 'AUDIT_IMAGE_ID': IMAGE, 'REFRESH_MANIFEST_SHA256': MANIFEST}
    worker_password = None
    private(S / 'migration.env.private', ''.join(key + '=' + value + '\n' for key, value in env.items()).encode())
    PHASE = 'restore_new_isolated_database'
    pg_guard()
    with (R / 'production.dump').open('rb') as dump:
        command('restore', [*BASE.DOCKER, 'exec', '-i', '-e', 'PGHOST=/var/run/postgresql',
            '-e', 'PGOPTIONS=-c statement_timeout=180000 -c lock_timeout=5000 -c timezone=UTC', PG_ID,
            'timeout', '-k', '10s', '1200s', 'pg_restore', '--exit-on-error', '--no-owner', '--no-privileges',
            '--no-comments', '--no-publications', '--no-subscriptions', '--no-tablespaces', '--role', 'modish_user',
            '-U', ADMIN, '-d', 'modish'], input_file=dump, timeout=1225)
    require(sql('SELECT version_num FROM alembic_version;') == '20261013_0025', 'restored_schema_0025_required')
    before_counts, before_hashes = full_integrity()
    BASE.json_file(S / 'restored-integrity.json', {'counts': before_counts, 'hashes': before_hashes})
    require(before_counts == clone['counts_before_migration'] and before_hashes == clone['row_integrity_before_migration'],
        'immutable_source_all_31_rows_must_match')
    PHASE = 'migrate_new_isolated_database'
    worker('migrate', S / 'migration.env.private', {}, ['-m', 'alembic', 'upgrade', '20261015_0028'], 600)
    require(sql('SELECT version_num FROM alembic_version;') == '20261015_0028', 'migrated_schema_0028_required')
    after_counts, after_hashes = full_integrity()
    BASE.json_file(S / 'migrated-integrity.json', {'counts': after_counts, 'hashes': after_hashes})
    require((after_counts, after_hashes) == (before_counts, before_hashes), 'all_31_normalized_rows_preserved_through_migration')
    PHASE = 'unmodified_production_importer_on_isolated_server'
    plan_dir, outcome = S / 'plan', S / 'outcome'
    plan_dir.mkdir(mode=0o700)
    outcome.mkdir(mode=0o700)
    plan = {'operation': 'build15_production_activation_plan', 'plan_id': args.exercise_id, 'run_id': RUN,
        'archive_sha256': ARCHIVE, 'image_id': IMAGE, 'snapshot_manifest_sha256': MANIFEST,
        'source_revision': '20261013_0025', 'target_revision': '20261015_0028',
        'production_database': {'host': 'postgres', 'port': 5432, 'name': 'modish', 'role': 'modish_user'}, 'feeds': FEEDS,
        'production_containers': {key: {'name': name, 'id': value[0], 'image': value[1]}
            for key, (name, value) in zip(('backend', 'postgres', 'redis'), BASE.PROD.items())},
        'approval_required': True, 'approval_token': APPROVAL, 'fixture_scope': 'new_isolated_server_only_no_production_authorization'}
    BASE.json_file(plan_dir / 'activation-plan.json', plan)
    env.update(PLAN_SHA256=sha(plan_dir / 'activation-plan.json'), PRODUCTION_APPROVAL_TOKEN=APPROVAL)
    private(S / 'import.env.private', ''.join(key + '=' + value + '\n' for key, value in env.items()).encode())
    env.clear()
    snapshot_guard()
    regular(R / 'catalog_refresh15_production_import.py', 131072, IMPORTER_SHA)
    mounts = {'/reviewed/catalog_refresh15_production_import.py': (str(R / 'catalog_refresh15_production_import.py'), False),
        '/plan': (str(plan_dir), False), '/feeds': (str(R / 'feeds'), False), '/outcome': (str(outcome), True)}
    raw = worker('import', S / 'import.env.private', mounts, ['/reviewed/catalog_refresh15_production_import.py'], 7200)
    result = json.loads(raw)
    require(result.get('scope') == 'build15_approved_four_source_production_import'
        and result.get('result') == 'production_import_committed' and result.get('run_id') == RUN
        and result.get('plan_id') == args.exercise_id and result.get('plan_sha256') == sha(plan_dir / 'activation-plan.json')
        and result.get('image_id') == IMAGE and result.get('archive_sha256') == ARCHIVE
        and result.get('snapshot_manifest_sha256') == MANIFEST and result.get('transaction_state') == 'committed'
        and result.get('needs_review') is False and result.get('committed_sources') == list(FEEDS)
        and result.get('existing_product_ids_preserved') is True and result.get('unaffected_rows_preserved') is True
        and {item['code']: item['rows'] for item in result.get('sources', [])} == ROWS, 'real_isolated_production_import_commit_required')
    regular(outcome / 'committed.json', 131072)
    require(json.loads((outcome / 'committed.json').read_bytes()) == result, 'durable_commit_report_must_match_stdout')
    require(sql('SELECT count(*) FROM catalog_sync_runs;') == str(before_counts['catalog_sync_runs'] + 4), 'exactly_four_committed_sync_runs_required')
    BASE.json_file(S / 'import-report.json', result)
    PHASE = 'final_isolation_and_immutability'
    network_guard()
    pg_guard()
    require(BASE.production_snapshot('exercise_production_after') == baseline, 'production_container_metadata_must_remain_unchanged')
    regular(R / 'production.dump', DUMP_BYTES, DUMP_SHA)
    regular(R / 'clone-report.json', 131072, CLONE_SHA)
    regular(R / 'catalog_refresh15_production_import.py', 131072, IMPORTER_SHA)
    snapshot_guard()
    report = {'scope': 'new_isolated_server_unmodified_production_importer', 'result': 'isolated_production_importer_real_db_pass',
        'exercise_id': args.exercise_id, 'run_id': RUN, 'archive_sha256': ARCHIVE, 'image_id': IMAGE,
        'importer_sha256': IMPORTER_SHA, 'snapshot_manifest_sha256': MANIFEST, 'dump_sha256': DUMP_SHA,
        'table_count': 31, 'all_source_full_rows_restored': True, 'migration_all_normalized_rows_preserved': True,
        'committed_sources': list(FEEDS), 'rows': sum(ROWS.values()), 'existing_product_ids_preserved': True,
        'unaffected_rows_preserved': True, 'production_sql_executed': False, 'production_container_metadata_unchanged': True,
        'network': NETWORK, 'network_internal': True, 'postgres_container_id': PG_ID,
        'production_network_connected': False, 'automatic_cleanup': False, 'automatic_retry': False,
        'private_fixture_retained': True, 'finished_at': datetime.now(timezone.utc).isoformat()}
    BASE.json_file(S / 'exercise-report.json', report)
    print(json.dumps(report))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        failure = {'scope': 'new_isolated_server_unmodified_production_importer', 'result': 'failed', 'phase': PHASE,
            'error_type': type(error).__name__, 'raw_error_omitted': True, 'production_sql_executed': False,
            'automatic_retry': False, 'automatic_cleanup': False, 'requires_review': True}
        if isinstance(error, RuntimeError) and re.fullmatch(r'[a-z][a-z0-9_]{0,100}', str(error)):
            failure['check'] = str(error)
        if STAGE_CREATED and BASE is not None and S is not None and S.is_dir():
            try:
                stop_worker()
            except Exception:
                failure['owned_worker_stop_requires_review'] = True
            try:
                BASE.json_file(S / 'failed.json', failure)
            except Exception:
                failure['ledger_write_requires_review'] = True
        print(json.dumps(failure))
        raise SystemExit(1) from None
