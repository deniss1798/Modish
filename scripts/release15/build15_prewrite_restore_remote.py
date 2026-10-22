"""MANUAL approved pre-writes recovery: final dump -> NEW DB -> legacy API.

Preparation only. Never execute without separately approved pinned recovery scope.
No downloads, drops, deletes, replay, automatic rollback or old API on schema0028.
Two database renames are deliberately separate bounded commands under maintenance;
their pair is NOT claimed atomic. Each has a durable intent and an OID proof.
An unknown rename/commit outcome leaves every database intact for manual review.
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
import shutil
from time import monotonic
from uuid import UUID

RUN = '9c4c8c7fe5a045068cde76d81df12104'
ARCHIVE = '5184f378245db0073154369f1b6e083c8d584cb9006788993cd4727866e26c24'
IMAGE = 'sha256:633bce227d3ce3277137c77f32f2c6e83bb338d91784803df424d7592a464aec'
MANIFEST = '5d2dde18bcb09cde948d044bc57d2178fd035aebc0698a0996d79dd5ab4ce23c'
BASE_SHA = 'e1eeb23e6be60e5de4230c07313f115434f2ef03974909182ef9db0249e9fc44'
PROXY_SHA = 'ae4e50a9260e6e0f955d17dd8b6b936772299c3744ab2196530b80650b30b2c6'
WORKER_SHA = 'b9bf37d9bb911f6354e7182922aa1d4acad78bc14f7d9f2f9632a4773ebc0688'
APPROVAL = 'APPROVE_BUILD15_PREWRITES_FINAL_BACKUP_RESTORE'
LEGACY_VERSION = '1.0.0-build14'
COMMAND = ['-m', 'uvicorn', 'app.main:app', '--host', '0.0.0.0', '--port', '8000']
KNOWN_REVISIONS = {'20261013_0025', '20261015_0026', '20261015_0027', '20261015_0028'}
BASE = PROXY = ROOT = STATE = ARGS = PLAN = FENCE = None
LOCK = None
DEADLINE = None
SEQUENCE = 0
PHASE = 'arguments'
STAGE_CREATED = False
RENAME_ATTEMPTED = OPEN_ATTEMPTED = False
OWNED_APIS = []
OLD_RUNTIME = API_ENV = None


def require(value, code):
    if not value:
        raise RuntimeError(code)


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def private(path, maximum=131072):
    require(path.is_file() and not path.is_symlink() and path.stat().st_uid == 0
        and path.stat().st_mode & 0o777 == 0o600 and 0 < path.stat().st_size <= maximum,
        'private_bounded_input_required')
    return path.read_bytes()


def load(path, expected, name):
    private(path)
    require(sha(path) == expected, 'pinned_reviewed_helper_required')
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def record(name, value):
    BASE.json_file(STATE / (name + '.json'), value)
    PROXY.syncdir(STATE)


def phase(value, **facts):
    global PHASE, SEQUENCE
    PHASE, SEQUENCE = value, SEQUENCE + 1
    record('phase-' + str(SEQUENCE).zfill(2) + '-' + value,
        {'phase': value, 'at': datetime.now(timezone.utc).isoformat(), 'recovery_id': ARGS.recovery_id,
         'activation_plan_sha256': ARGS.plan_sha256, **facts})


def command(label, argv, **options):
    global SEQUENCE
    SEQUENCE += 1
    remaining = DEADLINE - monotonic()
    require(remaining > 0, 'recovery_total_deadline_exceeded')
    options['timeout'] = min(options.get('timeout', 40), remaining)
    return BASE.run('recovery_' + str(SEQUENCE).zfill(4) + '_' + label, argv, **options)


def inspect(identity):
    return json.loads(command('inspect', [*BASE.DOCKER, 'inspect', identity]))[0]


def stable_infrastructure():
    for name in ('modish_postgres', 'modish_redis'):
        obj = inspect(name)
        require(obj['Id'] == BASE.PROD[name][0] and obj['Image'] == BASE.PROD[name][1]
            and obj['State']['Running'], 'pinned_production_infrastructure_required')
        if name == 'modish_postgres':
            env = dict(value.split('=', 1) for value in obj['Config'].get('Env', []) if '=' in value)
            require(not BASE.PG_ENV.intersection(env)
                and set(obj['NetworkSettings']['Networks']) == {'modish_default'}
                and 'postgres' in (obj['NetworkSettings']['Networks']['modish_default'].get('Aliases') or []),
                'production_postgres_alias_and_environment_required')


def sql(statement, *, database='postgres', write=False):
    require(database in {'postgres', 'modish', 'modish_recovery15_' + ARGS.recovery_id,
        'modish_failed15_' + ARGS.recovery_id}, 'fixed_recovery_database_required')
    stable_infrastructure()
    options = '-c statement_timeout=180000 -c lock_timeout=5000 -c timezone=UTC -c work_mem=4MB -c maintenance_work_mem=64MB'
    if not write:
        options += ' -c default_transaction_read_only=on'
    return command('sql', [*BASE.DOCKER, 'exec', '-i', '-e', 'PGHOST=/var/run/postgresql', '-e', 'PGOPTIONS=' + options,
        BASE.PROD['modish_postgres'][0], 'timeout', '-k', '5s', '195s', 'psql', '-X', '-qAt', '-U', 'modish_user',
        '-d', database, '--set', 'ON_ERROR_STOP=1'], data=statement.encode(), timeout=210).decode().strip()


def closed_history(continuation, failure=None):
    if continuation.exists():
        require(continuation.is_dir() and not continuation.is_symlink(), 'regular_continuation_directory_required')
        for path in continuation.rglob('*'):
            require(not path.is_symlink(), 'continuation_symlink_forbidden')
            require('writes_open' not in path.name and path.name not in {'proxy-open-intent.json', 'activation-report.json'},
                'public_writes_history_forbids_prewrite_restore')
    if failure is not None:
        require(failure.get('operation') == 'build15_approved_production_activation'
            and failure.get('status') == 'failed' and failure.get('public_writes_may_exist') is False,
            'known_prewrite_continuation_failure_required')
        require(failure.get('import_outcome_uncertain_requires_review') is False,
            'unknown_import_commit_requires_separate_manual_resolution')
    outcome = continuation / 'production-import-outcome'
    if (outcome / 'commit-intent.json').exists():
        require((outcome / 'committed.json').is_file(), 'unknown_import_commit_requires_separate_manual_resolution')
        committed = json.loads(private(outcome / 'committed.json'))
        require(committed.get('transaction_state') == 'committed' and committed.get('needs_review') is False
            and committed.get('plan_sha256') == ARGS.plan_sha256,
            'unknown_import_commit_requires_separate_manual_resolution')


def fenced():
    closed_history(ROOT / 'activation-continuation')
    PROXY.assert_fenced(BASE, ROOT, ARGS.proxy_plan_sha256, ARGS.plan_sha256, FENCE)


def no_clients(*, extra_database=None):
    allowed = {'modish', 'modish_recovery15_' + ARGS.recovery_id, 'modish_failed15_' + ARGS.recovery_id}
    require(extra_database is None or extra_database in allowed, 'fixed_recovery_database_required')
    names = '(' + ','.join("'" + name + "'" for name in sorted(allowed)) + ')'
    require(sql("SELECT count(*) FROM pg_stat_activity WHERE datname IN " + names +
        " AND pid<>pg_backend_pid() AND backend_type='client backend';") == '0',
        'writers_or_unaccounted_database_clients_present')


def table_proof(database, *, normalized=False, outside=False):
    tables = sql("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename;", database=database).splitlines()
    require(len(tables) == 31 and all(re.fullmatch(r'[a-z][a-z0-9_]{0,62}', name) for name in tables),
        'all_31_bounded_public_tables_required')
    codes = "('baon','shoppinglive','serginnetti','postmeridiem')"
    source_ids = '(SELECT id FROM public.product_sources WHERE code IN ' + codes + ')'
    predicates = {'products': 'source IS NULL OR source NOT IN ' + codes,
        'catalog_records': 'source_id IS NULL OR source_id NOT IN ' + source_ids,
        'product_sources': 'code IS NULL OR code NOT IN ' + codes,
        'catalog_sync_runs': 'source_id IS NULL OR source_id NOT IN ' + source_ids}
    result = {}
    for table in tables:
        payload = 'row_to_json(t)::text'
        if normalized:
            payload = 'to_jsonb(t)'
            if table == 'products':
                payload += " || jsonb_build_object('price',t.price::numeric(14,2),'old_price',t.old_price::numeric(14,2))"
            elif table == 'outfits':
                payload += " || jsonb_build_object('total_price',t.total_price::numeric(14,2))"
            elif table == 'users':
                payload += " - 'recommendation_revision'"
            elif table == 'alembic_version':
                payload += " - 'version_num'"
            payload = '(' + payload + ')::text'
        where = predicates.get(table, 'TRUE') if outside else 'TRUE'
        value = json.loads(sql("SELECT json_build_object('count',count(*),'rows',md5(coalesce(string_agg(md5(" + payload +
            "),'' ORDER BY md5(" + payload + ")),''))) FROM public.\"" + table + '\" t WHERE ' + where + ';', database=database))
        require(type(value.get('count')) is int and value['count'] >= 0
            and re.fullmatch(r'[0-9a-f]{32}', value.get('rows', '')), 'complete_aggregate_row_proof_required')
        result[table] = value
    return result


def current_user_data_unchanged(restored):
    revision = sql('SELECT version_num FROM alembic_version;', database='modish')
    require(revision in KNOWN_REVISIONS, 'known_failed_migration_revision_required')
    has_revision = sql("SELECT count(*) FROM information_schema.columns WHERE table_schema='public' "
        "AND table_name='users' AND column_name='recommendation_revision';", database='modish')
    require(has_revision in {'0', '1'} and (revision != '20261015_0028' or has_revision == '1'), 'expected_revision_column_required')
    if has_revision == '1':
        require(sql('SELECT count(*) FROM users WHERE recommendation_revision<>0 OR recommendation_revision IS NULL;',
            database='modish') == '0', 'user_recommendation_changes_forbid_backup_restore')
    before, after = table_proof(restored, normalized=True, outside=True), table_proof('modish', normalized=True, outside=True)
    require(before == after, 'new_user_or_unaffected_data_forbids_backup_restore')
    return revision


def ports(obj):
    return {(port, value.get('HostIp') or '0.0.0.0', value['HostPort'])
        for port, values in (obj['HostConfig'].get('PortBindings') or {}).items() for value in values}


def stop_deployment_writers():
    # Compose may have removed the old API before failing to create its
    # replacement. An exact empty inventory proves absence; inspect errors do
    # not. Recovery can then use the retained immutable runtime proposal.
    found = command('backend_name_inventory', [*BASE.DOCKER, 'ps', '--all', '--filter',
        'name=^/modish_backend$', '--format', '{{.ID}}']).decode().splitlines()
    require(len(found) <= 1 and all(re.fullmatch(r'[0-9a-f]{12,64}', value) for value in found),
        'bounded_exact_backend_inventory_required')
    current = inspect(found[0]) if found else None
    if current is not None:
        require(current['Name'] == '/modish_backend'
            and set(current['NetworkSettings']['Networks']) == {'modish_default'}, 'known_backend_network_required')
        mounts = {(m['Type'], m['Source'], m['Destination'], m['RW']) for m in current['Mounts']}
        require(mounts == {(m['Type'], m['Source'], m['Destination'], m['RW']) for m in OLD_RUNTIME['Mounts']},
            'known_backend_storage_required')
    if current is None:
        phase('backend_absence_verified', exact_name_inventory_empty=True)
    elif current['Id'] == BASE.PROD['modish_backend'][0]:
        require(current['Image'] == BASE.PROD['modish_backend'][1] and not current['State']['Running']
            and current['HostConfig']['RestartPolicy']['Name'] == 'no', 'old_api_must_already_be_fenced')
    else:
        labels = current['Config'].get('Labels') or {}
        require(current['Image'] == IMAGE and ports(current) == {('8000/tcp', '127.0.0.1', '8000')}
            and labels.get('com.docker.compose.project') == 'modish' and labels.get('com.docker.compose.service') == 'backend'
            and current['Config'].get('Entrypoint') in (None, []) and current['Config'].get('Cmd') == ['python', *COMMAND],
            'known_loopback_candidate_required')
        candidate_env = dict(value.split('=', 1) for value in current['Config'].get('Env', []))
        require(candidate_env.get('DATABASE_URL') == API_ENV['DATABASE_URL']
            and candidate_env.get('JWT_SECRET') == API_ENV['JWT_SECRET'] and candidate_env.get('APP_ENV') == 'production',
            'known_candidate_database_and_sessions_required')
        phase('candidate_stop_intent', container_id=current['Id'])
        command('candidate_restart_off', [*BASE.DOCKER, 'update', '--restart=no', current['Id']])
        if current['State']['Running']:
            command('candidate_stop', [*BASE.DOCKER, 'stop', '--time', '30', current['Id']], timeout=45)
        require(not inspect(current['Id'])['State']['Running'], 'candidate_api_must_stop')
    raw = command('deployment_workers', [*BASE.DOCKER, 'ps', '--all', '--filter',
        'label=org.modish.activation_plan=' + PLAN['plan_id'], '--format', '{{.ID}}'])
    for identity in raw.decode().splitlines():
        require(re.fullmatch(r'[0-9a-f]{12,64}', identity), 'bounded_worker_identity_required')
        obj = inspect(identity)
        labels = obj['Config'].get('Labels') or {}
        kind = labels.get('org.modish.activation_phase')
        require(kind in {'migration', 'import', 'private_api'} and obj['Image'] == IMAGE
            and labels.get('org.modish.activation_plan') == PLAN['plan_id']
            and obj['Name'] == '/modish_deploy15_' + kind + '_' + PLAN['plan_id']
            and set(obj['NetworkSettings']['Networks']) in ({'modish_default'}, set())
            and not obj['HostConfig'].get('PortBindings') and obj['HostConfig']['RestartPolicy']['Name'] == 'no',
            'exact_owned_deployment_worker_required')
        if obj['State']['Running']:
            phase('deployment_worker_stop_intent', container_id=obj['Id'], worker_kind=kind)
            command('deployment_worker_stop', [*BASE.DOCKER, 'stop', '--time', '20', obj['Id']], timeout=35)
            require(not inspect(obj['Id'])['State']['Running'], 'deployment_worker_must_stop')


def database_oids():
    names = ('modish', 'modish_recovery15_' + ARGS.recovery_id, 'modish_failed15_' + ARGS.recovery_id)
    return json.loads(sql("SELECT coalesce(json_object_agg(datname,oid),'{}'::json) FROM pg_database WHERE datname IN (" +
        ','.join("'" + name + "'" for name in names) + ');'))


def api_owned(item):
    obj = inspect(item['id'])
    require(obj['Id'] == item['id'] and obj['Image'] == BASE.PROD['modish_backend'][1]
        and obj['Name'] == '/' + item['name'] and obj['Config'].get('Entrypoint') == ['python']
        and obj['Config'].get('Cmd') == COMMAND
        and obj['Config'].get('Labels', {}).get('org.modish.prewrite_recovery') == ARGS.recovery_id
        and set(obj['NetworkSettings']['Networks']) == {'modish_default'}
        and ports(obj) == (set() if item['kind'] == 'shadow' else {('8000/tcp', '127.0.0.1', '8000')})
        and obj['HostConfig'].get('Privileged') is False
        and obj['HostConfig'].get('ReadonlyRootfs') is (
            True if item['kind'] == 'shadow' else OLD_RUNTIME['HostConfig']['ReadonlyRootfs'])
        and not obj['HostConfig'].get('Devices') and not obj['HostConfig'].get('DeviceRequests')
        and obj['HostConfig'].get('PidMode', '') == '' and obj['HostConfig'].get('IpcMode') in {'private', ''}
        and obj['HostConfig'].get('RestartPolicy', {}).get('Name') in (
            {'no'} if item['kind'] == 'shadow' else {'no', 'unless-stopped'})
        and obj['HostConfig'].get('Memory') == 1536 * 1024**2 and obj['HostConfig'].get('MemorySwap') == 1536 * 1024**2
        and obj['HostConfig'].get('NanoCpus') == 1000000000 and obj['HostConfig'].get('PidsLimit') == 128
        and 'ALL' in (obj['HostConfig'].get('CapDrop') or []) and not obj['HostConfig'].get('CapAdd')
        and any(value.startswith('no-new-privileges') for value in obj['HostConfig'].get('SecurityOpt', [])),
        'exact_owned_schema0025_legacy_api_required')
    environment = dict(value.split('=', 1) for value in obj['Config'].get('Env', []))
    require(hashlib.sha256(json.dumps(environment, sort_keys=True).encode()).hexdigest() == item['environment_sha256'],
        'legacy_api_environment_must_match')
    actual = {(m['Type'], m.get('Name') if m['Type'] == 'volume' else m['Source'], m['Destination'], m['RW']) for m in obj['Mounts']}
    expected = {(m['Type'], m.get('Name') if m['Type'] == 'volume' else m['Source'], m['Destination'], m['RW']) for m in OLD_RUNTIME['Mounts']}
    require(actual == expected, 'legacy_storage_preservation_required')
    return obj


def legacy_schema():
    require(sql('SELECT version_num FROM alembic_version;', database='modish') == '20261013_0025',
        'legacy_api_requires_restored_schema0025')


def create_legacy_api(kind):
    legacy_schema()
    name = 'modish_recovery14_' + kind + '_' + ARGS.recovery_id
    require(not command('legacy_name_absent', [*BASE.DOCKER, 'ps', '--all', '--filter', 'name=^/' + name + '$',
        '--format', '{{.ID}}']).strip(), 'new_legacy_api_name_required')
    environment = dict(API_ENV)
    if kind == 'shadow':
        environment['PGOPTIONS'] = '-c default_transaction_read_only=on -c statement_timeout=30000 -c lock_timeout=3000'
    else:
        require(not any(key.startswith('PG') for key in environment), 'public_legacy_pg_overrides_forbidden')
    path = STATE / (kind + '.private.env')
    require(all(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key) and isinstance(value, str)
        and not any(ord(c) < 32 for c in value) for key, value in environment.items()), 'single_line_legacy_environment_required')
    with path.open('x', encoding='utf-8') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(''.join(key + '=' + value + '\n' for key, value in sorted(environment.items())))
        stream.flush(); os.fsync(stream.fileno())
    argv = [*BASE.DOCKER, 'create', '--pull=never', '--name', name, '--network=modish_default', '--restart=no',
        '--cpus=1', '--memory=1536m', '--memory-swap=1536m', '--pids-limit=128', '--cap-drop=ALL',
        '--security-opt=no-new-privileges', '--env-file', str(path), '--entrypoint=python',
        '--label', 'org.modish.prewrite_recovery=' + ARGS.recovery_id]
    require(type(OLD_RUNTIME['HostConfig'].get('ReadonlyRootfs')) is bool,
        'known_original_root_filesystem_policy_required')
    if kind == 'shadow' or OLD_RUNTIME['HostConfig']['ReadonlyRootfs']:
        argv += ['--read-only']
    if kind == 'public':
        argv += ['--publish', '127.0.0.1:8000:8000']
    for mount in OLD_RUNTIME['Mounts']:
        require(mount['Type'] in {'bind', 'volume'}, 'known_legacy_mount_type_required')
        source = mount.get('Name') if mount['Type'] == 'volume' else mount['Source']
        require(all(',' not in value and '\n' not in value for value in (source, mount['Destination'])), 'simple_legacy_mount_required')
        argv += ['--mount', 'type=' + mount['Type'] + ',src=' + source + ',dst=' + mount['Destination'] + ('' if mount['RW'] else ',readonly')]
    identity = command('create_legacy_' + kind, [*argv, BASE.PROD['modish_backend'][1], *COMMAND]).decode().strip()
    require(re.fullmatch(r'[0-9a-f]{64}', identity), 'immutable_legacy_api_id_required')
    item = {'id': identity, 'name': name, 'kind': kind,
        'environment_sha256': hashlib.sha256(json.dumps(environment, sort_keys=True).encode()).hexdigest()}
    OWNED_APIS.append(item)
    obj = api_owned(item)
    require(not obj['State']['Running'], 'new_legacy_api_must_start_stopped')
    return item


def legacy_http_smoke(item):
    source = r'''
import json,time
from urllib.request import build_opener,ProxyHandler
from urllib.error import HTTPError
opener=build_opener(ProxyHandler({}))
for _ in range(25):
    try:
        with opener.open('http://127.0.0.1:8000/health',timeout=3) as response:
            body=json.loads(response.read(65537))
            assert response.status==200 and body.get('status')=='ok' and body.get('database')=='ok' and body.get('version')=='1.0.0-build14'
        break
    except Exception:time.sleep(1)
else:raise RuntimeError('legacy_health_not_ready')
try:opener.open('http://127.0.0.1:8000/profile',timeout=3)
except HTTPError as error:assert error.code==401
else:raise RuntimeError('legacy_auth_guard_failed')
print(json.dumps({'status':'passed','version':'1.0.0-build14','database':'ok','unauthenticated_profile':401,'user_fixtures_created':False}))
'''
    api_owned(item)
    result = json.loads(command('legacy_http_smoke', [*BASE.DOCKER, 'exec', '-i', item['id'], 'python', '-'], data=source.encode(), timeout=140))
    require(result == {'status': 'passed', 'version': LEGACY_VERSION, 'database': 'ok', 'unauthenticated_profile': 401,
        'user_fixtures_created': False}, 'legacy_health_and_auth_smoke_required')
    return result


def legacy_tls_smoke():
    source = r'''
import http.client,json,socket,ssl
sock=ssl.create_default_context().wrap_socket(socket.create_connection(('127.0.0.1',443),timeout=5),server_hostname='modish.org.ru')
connection=http.client.HTTPConnection('modish.org.ru',443,timeout=5);connection.sock=sock
try:
    connection.request('GET','/health',headers={'Host':'modish.org.ru'})
    response=connection.getresponse();raw=response.read(65537);assert len(raw)<=65536
    body=json.loads(raw);assert response.status==200 and body.get('status')=='ok' and body.get('database')=='ok' and body.get('version')=='1.0.0-build14'
    print(json.dumps({'status':200,'version':'1.0.0-build14','loopback_only':True,'tls_verified':True}))
finally:connection.close()
'''
    result = json.loads(command('legacy_tls', ['python3', '-'], data=source.encode(), timeout=20))
    require(result == {'status': 200, 'version': LEGACY_VERSION, 'loopback_only': True, 'tls_verified': True}, 'legacy_tls_health_required')


def main():
    global BASE, PROXY, ROOT, STATE, ARGS, PLAN, FENCE, LOCK, DEADLINE, STAGE_CREATED
    global OLD_RUNTIME, API_ENV, RENAME_ATTEMPTED, OPEN_ATTEMPTED
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('root', 'plan-sha256', 'final-backup-report-sha256', 'source-integrity-sha256',
                 'proxy-plan-sha256', 'recovery-id', 'approval-token'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--continuation-failure-sha256')
    ARGS = args = parser.parse_args()
    require(os.name == 'posix' and os.geteuid() == 0, 'linux_root_required')
    require(args.approval_token == APPROVAL and all(re.fullmatch(r'[0-9a-f]{64}', value) for value in
        (args.plan_sha256, args.final_backup_report_sha256, args.source_integrity_sha256, args.proxy_plan_sha256)),
        'explicit_recovery_approval_and_pinned_reports_required')
    require(re.fullmatch(r'[0-9a-f]{32}', args.recovery_id) and UUID(args.recovery_id).version == 4, 'new_uuid4_recovery_required')
    ROOT = Path(args.root)
    require(re.fullmatch(r'/opt/modish-releases/20261022-build15-activation-plan-[0-9a-f]{32}', str(ROOT))
        and ROOT.is_dir() and not ROOT.is_symlink() and ROOT.resolve() == ROOT
        and ROOT.stat().st_uid == 0 and ROOT.stat().st_mode & 0o777 == 0o700, 'private_reviewed_activation_root_required')
    raw = private(ROOT / 'activation-plan.json')
    require(hashlib.sha256(raw).hexdigest() == args.plan_sha256, 'pinned_activation_plan_required')
    PLAN = json.loads(raw)
    worker = load(ROOT / 'catalog_refresh15_production_import.py', WORKER_SHA, 'recovery_reviewed_plan_contract')
    worker.validate_plan_value(PLAN)
    require(ROOT.name.endswith(PLAN['plan_id']) and args.recovery_id != PLAN['plan_id'], 'matching_plan_and_new_recovery_uuid_required')
    prior = ROOT / 'final-backup-attempt'
    raw = private(prior / 'final-backup-report.json')
    require(hashlib.sha256(raw).hexdigest() == args.final_backup_report_sha256, 'pinned_final_backup_report_required')
    backup = json.loads(raw)
    require(backup.get('operation') == 'build15_approved_final_backup' and backup.get('status') == 'maintenance_final_backup_restore_verified'
        and backup.get('plan_sha256') == args.plan_sha256 and backup.get('plan_id') == PLAN['plan_id']
        and backup.get('run_id') == RUN and backup.get('image_id') == IMAGE and backup.get('archive_sha256') == ARCHIVE
        and backup.get('manifest_sha256') == MANIFEST and backup.get('source_revision') == '20261013_0025'
        and backup.get('table_count') == 31 and backup.get('restored_full_rows_preserved') is True
        and backup.get('source_rows_unchanged') is True and backup.get('writes_open') is False
        and backup.get('backend_stopped') is True and backup.get('backend_auto_restart_disabled') is True
        and backup.get('proxy_plan_sha256') == args.proxy_plan_sha256, 'verified_prewrite_final_backup_required')
    dump = prior / 'final-production.dump'
    require(dump.is_file() and not dump.is_symlink() and dump.stat().st_uid == 0 and dump.stat().st_mode & 0o777 == 0o600
        and 0 < dump.stat().st_size <= 8 * 1024**3 and dump.stat().st_size == backup['dump_bytes']
        and sha(dump) == backup['dump_sha256'], 'immutable_final_dump_required')
    integrity_raw = private(prior / 'source-integrity-before.json')
    require(hashlib.sha256(integrity_raw).hexdigest() == args.source_integrity_sha256, 'pinned_source_integrity_required')
    golden = json.loads(integrity_raw)
    require(isinstance(golden, dict) and len(golden) == 31 and all(re.fullmatch(r'[a-z][a-z0-9_]{0,62}', key)
        and type(value.get('count')) is int and value['count'] >= 0 and re.fullmatch(r'[0-9a-f]{32}', value.get('rows', ''))
        for key, value in golden.items()), 'all_31_source_proofs_required')
    continuation = ROOT / 'activation-continuation'
    failure = None
    if continuation.exists():
        require(args.continuation_failure_sha256 is not None and re.fullmatch(r'[0-9a-f]{64}', args.continuation_failure_sha256),
            'pinned_known_continuation_failure_required')
        failure_raw = private(continuation / 'activation-failed.json')
        require(hashlib.sha256(failure_raw).hexdigest() == args.continuation_failure_sha256, 'pinned_continuation_failure_required')
        failure = json.loads(failure_raw)
    closed_history(continuation, failure)
    require(not (ROOT / 'prewrite-recovery-started.json').exists() and not (ROOT / 'prewrite-recovery-started.json').is_symlink(),
        'previous_recovery_forbids_replay')
    current_raw = private(ROOT / 'current-runtime.private.json', 1024 * 1024)
    require(hashlib.sha256(current_raw).hexdigest() == PLAN['current_runtime_sha256'], 'pinned_original_api_runtime_required')
    OLD_RUNTIME = json.loads(current_raw)
    env_raw = private(ROOT / 'candidate.docker-env.private')
    require(hashlib.sha256(env_raw).hexdigest() == PLAN['candidate_environment_sha256'], 'pinned_api_environment_required')
    API_ENV = dict(line.split('=', 1) for line in env_raw.decode().splitlines())
    old_env = dict(value.split('=', 1) for value in OLD_RUNTIME['Config'].get('Env', []))
    worker.validate_database_url(API_ENV.get('DATABASE_URL'))
    require(API_ENV.get('JWT_SECRET') == old_env.get('JWT_SECRET') and len(API_ENV.get('JWT_SECRET', '')) >= 32
        and API_ENV.get('DATABASE_URL') == old_env.get('DATABASE_URL')
        and API_ENV.get('APP_ENV') == 'production' and API_ENV.get('STYLE_VISUAL_ENABLED') == 'false'
        and API_ENV.get('CLOSED_ALPHA_ENABLED') == 'false' and API_ENV.get('ALLOW_DEV_ANALYZE') == 'false'
        and not any(key.startswith('PG') for key in API_ENV), 'preserved_production_api_environment_required')
    rehearsal = Path('/opt/modish-releases/20261021-catalog-refresh15-' + RUN)
    BASE = load(rehearsal / 'catalog_refresh15_clone_remote.py', BASE_SHA, 'recovery_bounded_commands')
    PROXY = load(ROOT / 'build15_proxy_maintenance_remote.py', PROXY_SHA, 'recovery_exact_proxy_contract')
    PROXY.proposal(ROOT, args.proxy_plan_sha256, args.plan_sha256)
    fence_raw = private(prior / 'proxy-fence-report.json')
    require(hashlib.sha256(fence_raw).hexdigest() == backup['proxy_fence_report_sha256'], 'pinned_original_fence_required')
    FENCE = json.loads(fence_raw)
    LOCK = os.open('/opt/modish-releases/build15-activation.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    info = os.fstat(LOCK)
    require(info.st_uid == 0 and info.st_mode & 0o777 == 0o600 and info.st_nlink == 1, 'private_global_activation_lock_required')
    fcntl.flock(LOCK, fcntl.LOCK_EX | fcntl.LOCK_NB)
    require(not (ROOT / 'prewrite-recovery-started.json').exists(), 'previous_recovery_forbids_replay')
    os.umask(0o077)
    STATE = ROOT / ('prewrite-recovery-' + args.recovery_id)
    STATE.mkdir(mode=0o700)
    STAGE_CREATED = True
    PROXY.json_new(ROOT / 'prewrite-recovery-started.json', {'operation': 'manual_prewrite_recovery', 'recovery_id': args.recovery_id,
        'plan_sha256': args.plan_sha256, 'automatic_retry': False})
    BASE.R = STATE
    client = STATE / 'docker-client'; client.mkdir(mode=0o700); BASE.json_file(client / 'config.json', {})
    BASE.DOCKER.extend(['--config', str(client)])
    DEADLINE = monotonic() + 14400
    phase('preflight', separately_approved_recovery_marker=True, public_writes_open=False)
    stable_infrastructure(); fenced()
    require(OLD_RUNTIME['Id'] == BASE.PROD['modish_backend'][0] and OLD_RUNTIME['Image'] == BASE.PROD['modish_backend'][1],
        'exact_original_image_identity_required')
    image = json.loads(command('legacy_image', [*BASE.DOCKER, 'image', 'inspect', BASE.PROD['modish_backend'][1]]))[0]
    require(image['Id'] == BASE.PROD['modish_backend'][1], 'immutable_legacy_image_required')
    stop_deployment_writers(); no_clients()
    require(sql("SELECT current_user||':'||current_setting('port');") == 'modish_user:5432', 'actual_production_admin_identity_required')
    size = int(sql("SELECT pg_database_size('modish');"))
    require(0 < size <= 8 * 1024**3 and shutil.disk_usage(STATE).free >= size * 2 + 2 * 1024**3,
        'new_restore_disk_capacity_required')
    restored, failed = 'modish_recovery15_' + args.recovery_id, 'modish_failed15_' + args.recovery_id
    original_oids = database_oids()
    require(set(original_oids) == {'modish'}, 'strictly_new_recovery_database_names_required')
    phase('new_database_create_intent', database=restored, original_database_oid=original_oids['modish'])
    fenced(); no_clients()
    sql('CREATE DATABASE "' + restored + '" OWNER modish_user TEMPLATE template0;', write=True)
    sql('REVOKE ALL ON DATABASE "' + restored + '" FROM PUBLIC;', write=True)
    created_oids = database_oids()
    require(set(created_oids) == {'modish', restored} and created_oids['modish'] == original_oids['modish'], 'new_restore_oid_required')
    phase('new_database_restore_intent', database=restored, database_oid=created_oids[restored], dump_sha256=backup['dump_sha256'])
    stable_infrastructure(); fenced(); no_clients(extra_database=restored)
    with dump.open('rb') as source:
        command('restore_new_database', [*BASE.DOCKER, 'exec', '-i', '-e', 'PGHOST=/var/run/postgresql',
            '-e', 'PGOPTIONS=-c statement_timeout=180000 -c lock_timeout=5000 -c timezone=UTC -c maintenance_work_mem=64MB -c work_mem=4MB',
            BASE.PROD['modish_postgres'][0], 'timeout', '-k', '10s', '1800s', 'pg_restore', '--exit-on-error',
            '--no-owner', '--no-privileges', '--no-comments', '--no-publications', '--no-subscriptions', '--no-tablespaces',
            '-U', 'modish_user', '-d', restored], input_file=source, timeout=1825)
    require(sql('SELECT version_num FROM alembic_version;', database=restored) == '20261013_0025', 'restored_schema0025_required')
    require(table_proof(restored) == golden, 'all_final_backup_rows_must_restore_exactly')
    record('restored-source-proof', golden)
    previous_revision = current_user_data_unchanged(restored)
    failed_before = table_proof('modish')
    record('failed-database-full-proof-before', failed_before)
    phase('new_database_verified', database=restored, table_count=31, previous_revision=previous_revision, no_user_data_changes=True)
    require(sha(dump) == backup['dump_sha256'] and sha(prior / 'source-integrity-before.json') == args.source_integrity_sha256,
        'backup_artifacts_unchanged_before_cutover')
    # There is intentionally no transaction/atomicity claim across this pair.
    # An exception after either intent forbids automatic rerun or rename-back.
    fenced(); no_clients(extra_database=restored); current_user_data_unchanged(restored)
    require(database_oids() == created_oids, 'database_oids_unchanged_before_rename')
    phase('preserve_failed_database_rename_intent', original_name='modish', preserved_name=failed, original_oid=created_oids['modish'])
    RENAME_ATTEMPTED = True
    sql('ALTER DATABASE modish RENAME TO "' + failed + '";', write=True)
    first_oids = database_oids()
    require(first_oids == {failed: created_oids['modish'], restored: created_oids[restored]}, 'first_rename_outcome_must_be_confirmed')
    phase('restored_database_rename_intent', restored_name=restored, live_name='modish', restored_oid=created_oids[restored])
    fenced(); no_clients(extra_database=restored)
    sql('ALTER DATABASE "' + restored + '" RENAME TO modish;', write=True)
    require(database_oids() == {failed: created_oids['modish'], 'modish': created_oids[restored]}, 'second_rename_outcome_must_be_confirmed')
    require(sql('SELECT version_num FROM alembic_version;', database='modish') == '20261013_0025'
        and table_proof('modish') == golden and table_proof(failed) == failed_before,
        'restored_and_preserved_database_full_rows_required')
    phase('database_cutover_verified', live_database_oid=created_oids[restored], preserved_database=failed,
        preserved_database_oid=created_oids['modish'], all_rows_preserved=True, restored_schema='20261013_0025')
    shadow = create_legacy_api('shadow')
    fenced(); no_clients()
    phase('private_legacy_api_start_intent', container_id=shadow['id'], no_published_ports=True)
    legacy_schema()
    command('start_legacy_shadow', [*BASE.DOCKER, 'start', shadow['id']])
    legacy_http_smoke(shadow)
    require(table_proof('modish') == golden, 'private_legacy_smoke_must_not_write')
    command('stop_legacy_shadow', [*BASE.DOCKER, 'stop', '--time', '20', shadow['id']], timeout=35)
    require(not api_owned(shadow)['State']['Running'], 'shadow_api_must_stop')
    fenced(); no_clients()
    public = create_legacy_api('public')
    phase('loopback_legacy_api_start_intent', container_id=public['id'], schema='20261013_0025', public_writes_open=False)
    legacy_schema()
    command('start_legacy_loopback', [*BASE.DOCKER, 'start', public['id']])
    smoke = legacy_http_smoke(public)
    require(table_proof('modish') == golden and table_proof(failed) == failed_before, 'final_smoke_all_rows_unchanged_required')
    fenced()
    phase('legacy_restart_policy_intent', container_id=public['id'], restart='unless-stopped')
    command('legacy_restart_policy', [*BASE.DOCKER, 'update', '--restart=unless-stopped', public['id']])
    obj = api_owned(public)
    require(obj['State']['Running'] and obj['HostConfig']['RestartPolicy']['Name'] == 'unless-stopped', 'persistent_loopback_legacy_api_required')
    phase('recovery_writes_open_intent', container_id=public['id'], new_writes_may_exist_after_this_marker=True)
    OPEN_ATTEMPTED = True
    # FENCE was just reverified; remove ONLY the same zero-byte owned flag.
    flag = PROXY.FLAG
    require(flag.is_file() and not flag.is_symlink() and flag.stat().st_uid == 0 and flag.stat().st_size == 0
        and flag.stat().st_mode & 0o777 == 0o644
        and (flag.stat().st_dev, flag.stat().st_ino) == (FENCE['flag_device'], FENCE['flag_inode']), 'exact_fence_flag_required_before_open')
    flag.unlink(); PROXY.syncdir(flag.parent)
    legacy_tls_smoke()
    report = {'operation': 'build15_manual_prewrite_final_backup_restore', 'status': 'schema0025_legacy_api_restored_and_open',
        'recovery_id': args.recovery_id, 'plan_sha256': args.plan_sha256, 'final_backup_report_sha256': args.final_backup_report_sha256,
        'restored_dump_sha256': backup['dump_sha256'], 'restored_schema': '20261013_0025', 'api_version': LEGACY_VERSION,
        'backend_container_id': public['id'], 'backend_container_name': public['name'], 'backend_image_id': BASE.PROD['modish_backend'][1],
        'api_bind': '127.0.0.1:8000', 'api_jwt_preserved': True, 'failed_database_preserved': failed,
        'failed_database_oid': created_oids['modish'], 'restored_database_oid': created_oids[restored],
        'all_31_backup_tables_exact': True, 'all_failed_database_rows_preserved': True, 'new_user_data_loss': False,
        'automatic_retry': False, 'automatic_database_drop_or_delete': False, 'writes_open': True,
        'compose_files_modified': False, 'standalone_recovery_container_recorded': True,
        'finished_at': datetime.now(timezone.utc).isoformat()}
    record('recovery-report', report)
    print(json.dumps(report))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        failure = {'operation': 'build15_manual_prewrite_final_backup_restore', 'status': 'failed', 'phase': PHASE,
            'error_type': type(error).__name__, 'raw_error_omitted': True, 'requires_manual_review': True,
            'database_rename_attempted': RENAME_ATTEMPTED, 'rename_outcome_may_require_manual_review': RENAME_ATTEMPTED,
            'public_writes_may_exist': OPEN_ATTEMPTED, 'automatic_retry': False, 'automatic_rename_back_or_restore': False,
            'database_drop_or_delete': False, 'all_artifacts_preserved': True}
        if type(error) is RuntimeError and re.fullmatch(r'[a-z][a-z0-9_]{0,100}', str(error)):
            failure['check'] = str(error)
        if STAGE_CREATED and BASE is not None:
            if not OPEN_ATTEMPTED:
                DEADLINE = max(DEADLINE or 0, monotonic() + 120)
                for item in OWNED_APIS:
                    try:
                        obj = api_owned(item)
                        command('failure_restart_off', [*BASE.DOCKER, 'update', '--restart=no', item['id']])
                        if obj['State']['Running']:
                            command('failure_owned_stop', [*BASE.DOCKER, 'stop', '--time', '15', item['id']], timeout=30)
                    except Exception:
                        failure['owned_api_stop_requires_review'] = True
            try:
                record('recovery-failed', failure)
            except Exception:
                failure['ledger_write_requires_review'] = True
        print(json.dumps(failure))
        raise SystemExit(1) from None
    finally:
        if LOCK is not None:
            os.close(LOCK)
