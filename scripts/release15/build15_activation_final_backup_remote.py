"""Approved activation phase 1: stop the pinned API, final dump, verify restore.

PREPARATION ONLY until the owner explicitly approves the pinned plan. This file
has no SSH, downloads, production SQL writes, migration, import or API replacement.
On success the old API remains stopped, schema remains0025, and a pinned report
permits a separately reviewed continuation. On failure there is no automatic
restore/retry/restart. All generated role/database/data are in the isolated PG.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import shutil
from time import monotonic
from uuid import UUID

BASE_SHA = 'e1eeb23e6be60e5de4230c07313f115434f2ef03974909182ef9db0249e9fc44'
PROXY_SHA = 'ae4e50a9260e6e0f955d17dd8b6b936772299c3744ab2196530b80650b30b2c6'
RUN = '9c4c8c7fe5a045068cde76d81df12104'
ARCHIVE = '5184f378245db0073154369f1b6e083c8d584cb9006788993cd4727866e26c24'
IMAGE = 'sha256:633bce227d3ce3277137c77f32f2c6e83bb338d91784803df424d7592a464aec'
MANIFEST = '5d2dde18bcb09cde948d044bc57d2178fd035aebc0698a0996d79dd5ab4ce23c'
APPROVAL = 'APPROVE_BUILD15_AND_FOUR_SOURCE_PRODUCTION_IMPORT'
POLICY = {'APP_ENV': 'production', 'CLOSED_ALPHA_ENABLED': 'false',
          'STYLE_VISUAL_ENABLED': 'false', 'ALLOW_DEV_ANALYZE': 'false'}
BASE = None
PLAN = None
ROOT = None
STATE = None
LOCK = None
ARGS = None
BACKEND_STOP_ATTEMPTED = False
PROXY_FENCE_ATTEMPTED = False
BACKEND_STOPPED = False
SEQUENCE = 0
PHASE = 'arguments'
DEADLINE = None


def require(value, code):
    if not value:
        raise RuntimeError(code)


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def bounded_private(path, bound=131072):
    require(path.is_file() and not path.is_symlink() and path.stat().st_uid == 0
            and path.stat().st_mode & 0o777 == 0o600 and path.stat().st_size <= bound,
            'private_bounded_input_required')
    return path.read_bytes()


def record(name, value):
    BASE.json_file(STATE / (name + '.json'), value)
    directory = os.open(STATE, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def checkpoint(phase, **facts):
    global PHASE, SEQUENCE
    PHASE = phase
    SEQUENCE += 1
    record('phase-' + str(SEQUENCE).zfill(2) + '-' + phase,
           {'phase': phase, 'at': datetime.now(timezone.utc).isoformat(),
            'plan_sha256': ARGS.plan_sha256, **facts})


def command(label, argv, **options):
    remaining = DEADLINE - monotonic()
    require(remaining > 0, 'final_backup_total_deadline_exceeded')
    options['timeout'] = min(options.get('timeout', 40), remaining)
    return BASE.run(label, argv, **options)


def readonly_sql(statement, *, database='modish', test=False, write_test=False):
    global SEQUENCE
    require(not write_test or test, 'production_sql_writes_forbidden')
    SEQUENCE += 1
    label = 'activation_sql_' + str(SEQUENCE).zfill(4)
    container, role = (BASE.TEST_PG, BASE.ADMIN) if test else ('modish_postgres', 'modish_user')
    if test:
        BASE.guard_test(label + '_guard')
    else:
        obj = BASE.inspect(container, label + '_guard')
        require(obj['Id'] == BASE.PROD[container][0] and obj['Image'] == BASE.PROD[container][1]
                and obj['State']['Running'], 'pinned_production_postgres_required')
    options = '-c statement_timeout=180000 -c lock_timeout=5000 -c timezone=UTC'
    if not write_test:
        options += ' -c default_transaction_read_only=on'
    return command(label, [*BASE.DOCKER, 'exec', '-i', '-e', 'PGHOST=/var/run/postgresql',
        '-e', 'PGOPTIONS=' + options, container, 'timeout', '-k', '5s', '200s',
        'psql', '-X', '-qAt', '-U', role, '-d', database, '--set', 'ON_ERROR_STOP=1'],
        data=statement.encode(), timeout=215).decode().strip()


def assert_no_other_sessions():
    require(readonly_sql("SELECT count(*) FROM pg_stat_activity WHERE datname='modish' "
        "AND pid<>pg_backend_pid() AND backend_type='client backend';") == '0',
        'unaccounted_database_client_sessions_present')


def source_integrity(*, database='modish', test=False):
    tables = readonly_sql("SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname='public' "
                          'ORDER BY tablename;', database=database, test=test).splitlines()
    require(0 < len(tables) <= 100 and all(re.fullmatch(r'[a-z][a-z0-9_]{0,62}', table)
                                        for table in tables), 'bounded_application_tables_required')
    result = {}
    for table in tables:
        # Every business column enters each row hash. Sort only short hashes,
        # avoiding a sort of complete product JSON and primary-key assumptions.
        value = json.loads(readonly_sql("SELECT json_build_object('count',count(*),'rows',"
            "md5(coalesce(string_agg(md5(row_to_json(t)::text),'' ORDER BY md5(row_to_json(t)::text)),''))) "
            'FROM public."' + table + '" t;', database=database, test=test))
        require(isinstance(value['count'], int) and value['count'] >= 0
                and re.fullmatch(r'[0-9a-f]{32}', value['rows']), 'bounded_full_row_proof_required')
        result[table] = value
    return result


def backend_still_stopped(label):
    obj = BASE.inspect('modish_backend', label)
    require(obj['Id'] == BASE.PROD['modish_backend'][0]
            and obj['Image'] == BASE.PROD['modish_backend'][1]
            and not obj['State']['Running'] and obj['HostConfig']['RestartPolicy']['Name'] == 'no',
            'pinned_old_backend_must_remain_stopped_without_auto_restart')
    return obj


def actual_api_database_probe():
    # Use the RUNNING API's actual DATABASE_URL, not stale POSTGRES_PASSWORD
    # initialization metadata. This creates only a short read-only transaction.
    source = r'''
import json, os
from sqlalchemy import create_engine, text
engine = create_engine(os.environ['DATABASE_URL'], pool_pre_ping=False,
    connect_args={'connect_timeout': 10, 'options': '-c default_transaction_read_only=on -c statement_timeout=15000 -c lock_timeout=3000'})
try:
    with engine.connect() as connection:
        with connection.begin():
            connection.execute(text('SET TRANSACTION READ ONLY'))
            row = connection.execute(text("SELECT current_database(), current_user, current_setting('port'), current_setting('transaction_read_only'), (SELECT version_num FROM alembic_version)")).one()
            result = {'actual_database_matches': row[0] == 'modish', 'actual_role_matches': row[1] == 'modish_user',
                'port_matches': row[2] == '5432', 'read_only': row[3] == 'on', 'revision0025': row[4] == '20261013_0025'}
    print(json.dumps(result))
finally:
    engine.dispose()
'''
    value = json.loads(command('activation_api_actual_database_probe', [*BASE.DOCKER, 'exec', '-i',
        BASE.PROD['modish_backend'][0], 'python', '-'], data=source.encode(), timeout=35))
    require(value == {'actual_database_matches': True, 'actual_role_matches': True,
                     'port_matches': True, 'read_only': True, 'revision0025': True},
            'actual_api_credentials_readonly_probe_required')


def main():
    global BASE, PLAN, ROOT, STATE, LOCK, ARGS, BACKEND_STOP_ATTEMPTED, BACKEND_STOPPED, DEADLINE, PROXY_FENCE_ATTEMPTED
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('root', 'plan-id', 'plan-sha256', 'writer-review-sha256', 'proxy-plan-sha256', 'approval-token'):
        parser.add_argument('--' + name, required=True)
    ARGS = args = parser.parse_args()
    require(os.name == 'posix' and os.geteuid() == 0, 'linux_root_required')
    DEADLINE = monotonic() + 5400
    require(re.fullmatch(r'[0-9a-f]{32}', args.plan_id) and UUID(args.plan_id).version == 4,
            'uuid4_plan_required')
    require(all(re.fullmatch(r'[0-9a-f]{64}', value) for value in
                (args.plan_sha256, args.writer_review_sha256, args.proxy_plan_sha256)) and args.approval_token == APPROVAL,
            'pinned_plan_and_explicit_owner_approval_required')
    ROOT = Path(args.root)
    require(ROOT == Path('/opt/modish-releases/20261022-build15-activation-plan-' + args.plan_id)
            and ROOT.is_dir() and not ROOT.is_symlink() and ROOT.resolve() == ROOT
            and ROOT.stat().st_uid == 0 and ROOT.stat().st_mode & 0o777 == 0o700,
            'prepared_private_plan_stage_required')
    plan_raw = bounded_private(ROOT / 'activation-plan.json')
    require(hashlib.sha256(plan_raw).hexdigest() == args.plan_sha256, 'external_plan_hash_required')
    PLAN = json.loads(plan_raw)
    require(PLAN.get('operation') == 'build15_production_activation_plan' and PLAN.get('plan_id') == args.plan_id
            and PLAN.get('run_id') == RUN and PLAN.get('archive_sha256') == ARCHIVE
            and PLAN.get('image_id') == IMAGE and PLAN.get('snapshot_manifest_sha256') == MANIFEST
            and PLAN.get('source_revision') == '20261013_0025'
            and PLAN.get('target_revision') == '20261015_0028'
            and PLAN.get('approval_required') is True and PLAN.get('approval_token') == APPROVAL,
            'exact_reviewed_plan_contract_required')
    require(PLAN.get('production_database') == {'host': 'postgres', 'port': 5432,
            'name': 'modish', 'role': 'modish_user'}, 'exact_production_target_required')
    require(sha(ROOT / 'candidate.docker-env.private') == PLAN['candidate_environment_sha256']
            and sha(ROOT / 'candidate.compose.private.json') == PLAN['candidate_override_sha256']
            and sha(ROOT / 'current-runtime.private.json') == PLAN['current_runtime_sha256']
            and sha(ROOT / 'current-compose.private.yml') == PLAN['current_compose_sha256'],
            'private_runtime_artifacts_unchanged_required')
    old = json.loads(bounded_private(ROOT / 'current-runtime.private.json', 1024 * 1024))
    require(sha(Path('/opt/Modish/docker-compose.yml')) == PLAN['current_compose_sha256'],
            'live_compose_changed_since_review')
    writer_raw = bounded_private(ROOT / 'writers-review.json', 16384)
    require(hashlib.sha256(writer_raw).hexdigest() == args.writer_review_sha256, 'external_writer_review_hash_required')
    writers = json.loads(writer_raw)
    require(writers.get('operation') == 'build15_known_writers_review'
            and writers.get('plan_id') == args.plan_id and writers.get('plan_sha256') == args.plan_sha256
            and writers.get('all_known_writers_inventoried') is True
            and writers.get('uncovered_scheduler_cases_reviewed') is True
            and writers.get('known_writer_containers') == [] and writers.get('known_writer_units') == []
            and writers.get('maintenance_method') == 'stop_pinned_backend',
            'reviewed_no_other_known_writers_required')
    # Do not invent stop commands for arbitrary cron/systemd/wrapper names.
    # Any discovered writer requires a reviewed extension before this can run.
    STATE = ROOT / 'final-backup-attempt'
    require(not STATE.exists(), 'previous_attempt_forbids_replay')
    BASE_path = Path('/opt/modish-releases/20261021-catalog-refresh15-' + RUN) / 'catalog_refresh15_clone_remote.py'
    require(BASE_path.is_file() and not BASE_path.is_symlink() and sha(BASE_path) == BASE_SHA,
            'reviewed_bounded_command_module_required')
    spec = importlib.util.spec_from_file_location('build15_pinned_clone_utilities', BASE_path)
    BASE = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(BASE)
    proxy_path = ROOT / 'build15_proxy_maintenance_remote.py'
    require(proxy_path.is_file() and not proxy_path.is_symlink() and sha(proxy_path) == PROXY_SHA,
            'reviewed_proxy_helper_required')
    proxy_spec = importlib.util.spec_from_file_location('build15_proxy_utilities', proxy_path)
    proxy = importlib.util.module_from_spec(proxy_spec); proxy_spec.loader.exec_module(proxy)
    proxy.proposal(ROOT, args.proxy_plan_sha256, args.plan_sha256)
    expected_containers = {key: {'name': name, 'id': value[0], 'image': value[1]} for key, (name, value)
        in zip(('backend', 'postgres', 'redis'), BASE.PROD.items())}
    require(PLAN.get('production_containers') == expected_containers, 'exact_container_plan_identity_required')
    # Root-owned single-host lock complements the durable one-attempt ledger.
    lock_path = Path('/opt/modish-releases/build15-activation.lock')
    LOCK = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    lock_stat = os.fstat(LOCK)
    require(lock_stat.st_uid == 0 and lock_stat.st_mode & 0o777 == 0o600 and lock_stat.st_nlink == 1,
            'private_activation_lock_required')
    fcntl.flock(LOCK, fcntl.LOCK_EX | fcntl.LOCK_NB)
    os.umask(0o077)
    STATE.mkdir(mode=0o700)
    proxy.syncdir(ROOT)
    BASE.R = STATE
    client = STATE / 'docker-client'
    client.mkdir(mode=0o700)
    BASE.json_file(client / 'config.json', {})
    BASE.DOCKER.extend(['--config', str(client)])
    checkpoint('preflight_started', owner_approval_marker_supplied=True, database_writes=False)
    baseline = BASE.production_snapshot('activation_baseline')
    actual_backend = BASE.inspect('modish_backend', 'activation_backend_runtime')
    require(actual_backend['Config'] == old['Config'] and actual_backend['HostConfig'] == old['HostConfig']
            and actual_backend['Mounts'] == old['Mounts'], 'backend_runtime_changed_since_plan')
    current_env = dict(value.split('=', 1) for value in actual_backend['Config'].get('Env', []) if '=' in value)
    require(PLAN.get('preserve_api_database_credentials') is True
            and hashlib.sha256(current_env.get('DATABASE_URL', '').encode()).hexdigest()
                == PLAN.get('api_database_url_sha256'), 'live_api_database_credentials_preserved_required')
    actual_api_database_probe()
    require(readonly_sql('SELECT version_num FROM alembic_version;') == '20261013_0025',
            'production_schema_0025_required')
    require(readonly_sql("SELECT current_database()||':'||current_user||':'||current_setting('port');")
            == 'modish:modish_user:5432', 'production_database_identity_required')
    BASE.guard_test('activation_test_isolation')
    network = json.loads(command('activation_test_network', [*BASE.DOCKER, 'network', 'inspect', BASE.NETWORK]))[0]
    require(network.get('Internal') is True, 'internal_restore_network_required')
    require(all(BASE.NETWORK not in BASE.inspect(name, 'activation_net_' + str(index))['NetworkSettings']['Networks']
                for index, name in enumerate(BASE.PROD)), 'production_off_test_network_required')
    source_size = int(readonly_sql('SELECT pg_database_size(current_database());'))
    require(0 < source_size <= 8 * 1024**3 and shutil.disk_usage(STATE).free >= source_size * 3 + 2 * 1024**3,
            'final_backup_disk_space_required')
    database, role = 'modish_restore15_' + args.plan_id, 'restore15_' + args.plan_id
    require(readonly_sql("SELECT count(*) FROM pg_database WHERE datname='" + database + "';",
            database=BASE.ADMIN, test=True) == '0', 'new_restore_database_required')
    require(readonly_sql("SELECT count(*) FROM pg_roles WHERE rolname='" + role + "';",
            database=BASE.ADMIN, test=True) == '0', 'new_restore_role_required')
    checkpoint('preflight_passed', production_baseline=baseline, source_database_bytes=source_size)
    checkpoint('proxy_fence_about_to_start', proxy_plan_sha256=args.proxy_plan_sha256)
    PROXY_FENCE_ATTEMPTED = True
    fence = proxy.apply_fence(BASE, STATE, ROOT, args.proxy_plan_sha256, args.plan_sha256, args.approval_token)
    checkpoint('backend_stop_about_to_start', backend_id=BASE.PROD['modish_backend'][0],
               known_other_writers=0, external_requests_will_be_unavailable=True)
    require(DEADLINE - monotonic() >= 90, 'time_for_guarded_backend_stop_required')
    BACKEND_STOP_ATTEMPTED = True
    command('activation_backend_stop', [*BASE.DOCKER, 'stop', '--time', '45', BASE.PROD['modish_backend'][0]], timeout=60)
    command('activation_disable_old_restart', [*BASE.DOCKER, 'update', '--restart=no', BASE.PROD['modish_backend'][0]])
    backend_still_stopped('activation_backend_stopped')
    BACKEND_STOPPED = True
    assert_no_other_sessions()
    checkpoint('writers_fenced', backend_stopped=True, no_other_database_clients=True)
    source_before = source_integrity()
    record('source-integrity-before', source_before)
    assert_no_other_sessions()
    dump = STATE / 'final-production.dump'
    checkpoint('final_dump_about_to_start', schema='20261013_0025')
    with dump.open('xb') as stream:
        os.fchmod(stream.fileno(), 0o600)
        command('activation_final_dump', [*BASE.DOCKER, 'exec', '-e', 'PGHOST=/var/run/postgresql', '-e',
            'PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=300000 -c lock_timeout=5000 -c timezone=UTC',
            'modish_postgres', 'timeout', '-k', '10s', '900s', 'pg_dump', '-Fc', '--no-owner', '--no-privileges',
            '--lock-wait-timeout=5s', '-U', 'modish_user', '-d', 'modish'], output_file=stream,
            timeout=925, max_stdout=8 * 1024**3)
    with dump.open('rb') as stream:
        require(stream.read(5) == b'PGDMP', 'completed_custom_dump_required')
    dump_sha = sha(dump)
    source_after = source_integrity()
    require(source_after == source_before, 'production_rows_changed_during_final_dump')
    assert_no_other_sessions()
    backend_still_stopped('activation_before_restore')
    checkpoint('final_dump_verified', dump_sha256=dump_sha, dump_bytes=dump.stat().st_size,
               production_rows_unchanged=True)
    password = secrets.token_urlsafe(48)
    # Test-only credentials go to a private artifact for subsequent inspection.
    BASE.json_file(STATE / 'restore-identity.private.json', {'database': database, 'role': role, 'password': password})
    readonly_sql('CREATE ROLE "' + role + '" LOGIN PASSWORD \'' + password +
                 "' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 10;",
                 database=BASE.ADMIN, test=True, write_test=True)
    password = None
    readonly_sql('CREATE DATABASE "' + database + '" OWNER "' + role + '" TEMPLATE template0;',
                 database=BASE.ADMIN, test=True, write_test=True)
    readonly_sql('REVOKE ALL ON DATABASE "' + database + '" FROM PUBLIC;',
                 database=BASE.ADMIN, test=True, write_test=True)
    checkpoint('new_restore_database_created', database=database, role=role)
    BASE.guard_test('activation_restore_target_guard')
    with dump.open('rb') as source:
        command('activation_verify_restore', [*BASE.DOCKER, 'exec', '-i', '-e', 'PGHOST=/var/run/postgresql', '-e',
            'PGOPTIONS=-c statement_timeout=180000 -c lock_timeout=5000 -c timezone=UTC', BASE.TEST_PG,
            'timeout', '-k', '10s', '1200s', 'pg_restore', '--exit-on-error', '--no-owner', '--no-privileges',
            '--no-comments', '--no-publications', '--no-subscriptions', '--no-tablespaces',
            '--role', role, '-U', BASE.ADMIN, '-d', database], input_file=source, timeout=1225)
    require(readonly_sql('SELECT version_num FROM alembic_version;', database=database, test=True)
            == '20261013_0025', 'final_restore_schema_0025_required')
    restored = source_integrity(database=database, test=True)
    require(restored == source_before, 'final_restored_full_rows_mismatch')
    require(sha(dump) == dump_sha, 'final_dump_changed_after_restore')
    require(source_integrity() == source_before, 'production_changed_while_final_restore_checked')
    assert_no_other_sessions()
    backend_still_stopped('activation_final_old_api_stopped')
    require(readonly_sql('SELECT version_num FROM alembic_version;') == '20261013_0025',
            'production_schema_changed_before_continuation')
    proxy.assert_fenced(BASE, ROOT, args.proxy_plan_sha256, args.plan_sha256, fence)
    checkpoint('final_backup_restore_verified', table_count=len(restored), dump_sha256=dump_sha,
               schema='20261013_0025', database=database, backend_stopped=True)
    report = {'operation': 'build15_approved_final_backup', 'status': 'maintenance_final_backup_restore_verified',
        'plan_id': args.plan_id, 'plan_sha256': args.plan_sha256, 'run_id': RUN,
        'writer_review_sha256': args.writer_review_sha256, 'image_id': IMAGE, 'archive_sha256': ARCHIVE,
        'proxy_plan_sha256': args.proxy_plan_sha256,
        'proxy_fence_report_sha256': sha(STATE / 'proxy-fence-report.json'), 'backend_auto_restart_disabled': True,
        'manifest_sha256': MANIFEST, 'dump_sha256': dump_sha, 'dump_bytes': dump.stat().st_size,
        'source_revision': '20261013_0025', 'restore_database': database, 'restore_role': role,
        'restored_full_rows_preserved': True, 'source_rows_unchanged': True, 'table_count': len(restored),
        'backend_stopped': True, 'production_schema_migrated': False, 'production_import_started': False,
        'writes_open': False, 'automatic_retry': False, 'automatic_restart_or_restore': False,
        'continuation_required': True, 'finished_at': datetime.now(timezone.utc).isoformat()}
    record('final-backup-report', report)
    print(json.dumps(report))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        report = {'operation': 'build15_approved_final_backup', 'status': 'failed', 'phase': PHASE,
            'error_type': type(error).__name__, 'raw_error_omitted': True,
            'backend_stop_attempted': BACKEND_STOP_ATTEMPTED, 'backend_stopped_verified': BACKEND_STOPPED,
            'proxy_fence_attempted': PROXY_FENCE_ATTEMPTED,
            'production_sql_writes_by_helper': False, 'production_schema_migrated': False,
            'automatic_retry': False, 'automatic_restart_or_restore': False, 'requires_review': True}
        if type(error) is RuntimeError and re.fullmatch(r'[a-z][a-z0-9_]{0,100}', str(error)):
            report['check'] = str(error)
        if BASE is not None and STATE is not None and STATE.is_dir():
            try:
                record('failure', report)
            except Exception:
                report['ledger_write_requires_review'] = True
        print(json.dumps(report))
        raise SystemExit(1) from None
    finally:
        if LOCK is not None:
            os.close(LOCK)
