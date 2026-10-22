"""Approved build15 continuation after a VERIFIED final backup under maintenance.

PREPARATION ONLY: do not execute until the owner approves the exact package/plan.
No SSH/downloads/timers. Migration, import, private API smoke and public start
are separate durable phases. The old API is NEVER started on schema0028.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
from time import monotonic

BACKUP_HELPER_SHA = '8b54caa30bf96036b8c9e28108794dc5ead29ce654b0491d0653fb2ee22acfba'
PROXY_HELPER_SHA = 'ae4e50a9260e6e0f955d17dd8b6b936772299c3744ab2196530b80650b30b2c6'
BASE_SHA = 'e1eeb23e6be60e5de4230c07313f115434f2ef03974909182ef9db0249e9fc44'
IMPORTER_SHA = 'b9bf37d9bb911f6354e7182922aa1d4acad78bc14f7d9f2f9632a4773ebc0688'
RUN = '9c4c8c7fe5a045068cde76d81df12104'
ARCHIVE = '5184f378245db0073154369f1b6e083c8d584cb9006788993cd4727866e26c24'
IMAGE = 'sha256:633bce227d3ce3277137c77f32f2c6e83bb338d91784803df424d7592a464aec'
MANIFEST = '5d2dde18bcb09cde948d044bc57d2178fd035aebc0698a0996d79dd5ab4ce23c'
APPROVAL = 'APPROVE_BUILD15_AND_FOUR_SOURCE_PRODUCTION_IMPORT'
WORKER_JWT = 'unused-build15-catalog-import-worker-no-auth'
COMMAND = ['python', '-m', 'uvicorn', 'app.main:app', '--host', '0.0.0.0', '--port', '8000']
POLICY = {'APP_ENV': 'production', 'CLOSED_ALPHA_ENABLED': 'false',
          'STYLE_VISUAL_ENABLED': 'false', 'ALLOW_DEV_ANALYZE': 'false'}
FEEDS = {'baon': '19982', 'shoppinglive': '25461', 'serginnetti': '26417', 'postmeridiem': '25200'}
ROWS = {'baon': 9318, 'shoppinglive': 43402, 'serginnetti': 17065, 'postmeridiem': 378}
BASE = UTIL = PLAN = ROOT = STATE = ARGS = None
LOCK = None
PHASE = 'arguments'
SEQUENCE = 0
OWNED = []
MIGRATION_ATTEMPTED = IMPORT_ATTEMPTED = PUBLIC_OPEN_ATTEMPTED = False
IMPORT_COMMITTED = False


def require(value, code):
    if not value:
        raise RuntimeError(code)


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def private(path, maximum=131072):
    require(path.is_file() and not path.is_symlink() and path.stat().st_uid == 0
            and path.stat().st_mode & 0o777 == 0o600 and path.stat().st_size <= maximum,
            'private_bounded_input_required')
    return path.read_bytes()


def load(path, expected, name):
    require(path.is_file() and not path.is_symlink() and sha(path) == expected, 'pinned_helper_required')
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def record(name, value):
    BASE.json_file(STATE / (name + '.json'), value)
    fd = os.open(STATE, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def phase(value, **facts):
    global PHASE, SEQUENCE
    PHASE, SEQUENCE = value, SEQUENCE + 1
    record('phase-' + str(SEQUENCE).zfill(2) + '-' + value,
        {'phase': value, 'at': datetime.now(timezone.utc).isoformat(), 'plan_sha256': ARGS.plan_sha256, **facts})


def command(label, argv, **options):
    return UTIL.command(label, argv, **options)


def old_stopped(label):
    return UTIL.backend_still_stopped(label)


def table_proof():
    tables = UTIL.readonly_sql("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename;").splitlines()
    require(0 < len(tables) <= 100, 'bounded_public_tables_required')
    result = {}
    for table in tables:
        require(re.fullmatch(r'[a-z][a-z0-9_]{0,62}', table), 'simple_table_identifier_required')
        payload = 'to_jsonb(t)'
        if table == 'products':
            payload += " || jsonb_build_object('price',t.price::numeric(14,2),'old_price',t.old_price::numeric(14,2))"
        elif table == 'outfits':
            payload += " || jsonb_build_object('total_price',t.total_price::numeric(14,2))"
        elif table == 'users':
            payload += " - 'recommendation_revision'"
        elif table == 'alembic_version':
            payload += " - 'version_num'"
        result[table] = json.loads(UTIL.readonly_sql("SELECT json_build_object('count',count(*),'rows',"
            "md5(coalesce(string_agg(md5((" + payload + ")::text),'' ORDER BY md5((" + payload +
            ")::text)),''))) FROM public.\"" + table + '\" t;'))
    return result


def schema0028():
    require(UTIL.readonly_sql('SELECT version_num FROM alembic_version;') == '20261015_0028', 'schema0028_required')
    rows = json.loads(UTIL.readonly_sql("SELECT json_agg(json_build_object('table',table_name,'column',column_name,"
        "'type',data_type,'precision',numeric_precision,'scale',numeric_scale,'nullable',is_nullable,'default',column_default)) "
        "FROM information_schema.columns WHERE table_schema='public' AND ((table_name='products' AND column_name IN ('price','old_price')) "
        "OR (table_name='outfits' AND column_name='total_price') OR (table_name='fit_profiles' AND column_name='gender_target') "
        "OR (table_name='users' AND column_name='recommendation_revision'));"))
    require(isinstance(rows, list) and len(rows) == 5, 'five_migration_columns_required')
    for item in rows:
        if item['column'] in {'price', 'old_price', 'total_price'}:
            require((item['type'], item['precision'], item['scale']) == ('numeric', 14, 2), 'exact_decimal_type_required')
        elif item['column'] == 'gender_target':
            require(item['nullable'] == 'YES' and item['default'] is None, 'nullable_gender_no_default_required')
        else:
            require(item['type'] == 'integer' and item['nullable'] == 'NO' and item['default'] in {'0', '0::integer'},
                    'revision_default_required')


def envfile(name, values):
    require(all(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key) and isinstance(value, str)
                and not any(ord(char) < 32 for char in value) for key, value in values.items()),
            'single_line_worker_environment_required')
    path = STATE / name
    with path.open('x', encoding='utf-8') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(''.join(key + '=' + value + '\n' for key, value in sorted(values.items())))
        stream.flush()
        os.fsync(stream.fileno())
    return path


def owned(obj, item):
    labels = obj['Config'].get('Labels') or {}
    require(obj['Id'] == item['id'] and obj.get('Name') == '/' + item['name'] and obj['Image'] == IMAGE
            and labels.get('org.modish.activation_plan') == PLAN['plan_id']
            and labels.get('org.modish.activation_phase') == item['phase']
            and obj['HostConfig'].get('NetworkMode') == 'modish_default'
            and set(obj['NetworkSettings']['Networks']) in ({'modish_default'}, set())
            and not obj['HostConfig'].get('PortBindings')
            and obj['Config'].get('Entrypoint') == ['python']
            and obj['Config'].get('Cmd') == item['cmd'], 'owned_private_worker_identity_required')
    host = obj['HostConfig']
    require(host.get('NanoCpus') == 1000000000 and host.get('Memory') == item['memory_bytes']
            and host.get('MemorySwap') == item['memory_bytes'] and host.get('PidsLimit') == 128
            and host.get('ReadonlyRootfs') is True and host.get('Privileged') is False
            and not host.get('Devices') and not host.get('DeviceRequests')
            and host.get('PidMode', '') == '' and host.get('IpcMode') in {'private', ''}
            and host.get('RestartPolicy', {}).get('Name') == 'no'
            and 'ALL' in (host.get('CapDrop') or []) and not host.get('CapAdd')
            and any(value.startswith('no-new-privileges') for value in host.get('SecurityOpt', [])),
            'owned_private_worker_resource_and_security_limits_required')
    actual_mounts = {(value['Source'], value['Destination'], value['RW']) for value in obj['Mounts']}
    require(actual_mounts == set(item['mounts']), 'owned_private_worker_mounts_required')
    return obj


def create_worker(kind, cmd, environment, mounts=(), memory='1536m'):
    require(memory in {'1024m', '1536m'}, 'fixed_worker_memory_bound_required')
    name = 'modish_deploy15_' + kind + '_' + PLAN['plan_id']
    path = envfile(kind + '.private.env', environment)
    args = [*BASE.DOCKER, 'create', '--pull=never', '--name', name, '--cpus=1', '--memory=' + memory,
        '--memory-swap=' + memory, '--pids-limit=128', '--cap-drop=ALL', '--security-opt=no-new-privileges',
        '--read-only', '--network=modish_default', '--env-file', str(path), '--entrypoint=python',
        '--label', 'org.modish.activation_plan=' + PLAN['plan_id'], '--label', 'org.modish.activation_phase=' + kind]
    for source, destination, writable in mounts:
        require(',' not in str(source) and ':' not in str(source) and '\n' not in str(source), 'simple_mount_source_required')
        args += ['-v', str(source) + ':' + destination + (':rw' if writable else ':ro')]
    identity = command('create_' + kind, [*args, IMAGE, *cmd]).decode().strip()
    require(re.fullmatch(r'[0-9a-f]{64}', identity), 'full_worker_container_id_required')
    item = {'id': identity, 'name': name, 'phase': kind, 'cmd': cmd,
            'memory_bytes': int(memory[:-1]) * 1024 * 1024,
            'mounts': [(str(source), destination, writable) for source, destination, writable in mounts]}
    OWNED.append(item)
    owned(BASE.inspect(identity, 'created_' + kind), item)
    return item


def cleanup_owned_workers():
    """Independent45s inspection/stop budget, even after workflow timeout."""
    end, review = monotonic() + 45, False
    for index, item in enumerate(OWNED):
        remaining = end - monotonic()
        if remaining <= 0:
            review = True
            continue
        try:
            raw = BASE.run('cleanup_inspect_' + str(index), [*BASE.DOCKER, 'inspect', item['id']],
                timeout=min(6, remaining), max_stdout=1024*1024, max_stderr=131072)
            values = json.loads(raw)
            require(isinstance(values, list) and len(values) == 1, 'single_cleanup_object_required')
            obj = owned(values[0], item)
            if obj['State']['Running']:
                remaining = end - monotonic()
                require(remaining > 0, 'cleanup_budget_exhausted')
                BASE.run('cleanup_stop_' + str(index), [*BASE.DOCKER, 'stop', '--time', '5', item['id']],
                    timeout=min(8, remaining), max_stdout=131072, max_stderr=131072)
        except Exception:
            review = True
    return review


def execute_worker(item, timeout):
    phase(item['phase'] + '_about_to_start', container_id=item['id'])
    output = STATE / (item['phase'] + '.private-output.json')
    with output.open('xb') as stream:
        os.fchmod(stream.fileno(), 0o600)
        command('execute_' + item['phase'], [*BASE.DOCKER, 'start', '--attach', item['id']], output_file=stream,
                timeout=timeout)
    obj = owned(BASE.inspect(item['id'], 'finished_' + item['phase']), item)
    require(not obj['State']['Running'] and obj['State']['ExitCode'] == 0, 'worker_success_required')
    return output


def private_http_smoke(item, sku, price, label='private_http_smoke'):
    source = r'''
import json, sys, time
from decimal import Decimal
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, build_opener, ProxyHandler
opener = build_opener(ProxyHandler({}))
def fetch(path, method='GET'):
    try:
        response = opener.open(Request('http://127.0.0.1:8000'+path,method=method),timeout=3)
    except HTTPError as error:
        response = error
    with response:
        raw = response.read(524289)
        assert len(raw)<=524288
        return response.status,json.loads(raw)
for _ in range(20):
    try:
        status,body=fetch('/health/ready')
        if status==200 and body.get('version')=='1.0.1-build15' and body.get('status')=='ok' and body.get('database')=='ok': break
    except Exception: pass
    time.sleep(1)
else: raise RuntimeError('readiness_not_achieved')
status,body=fetch('/catalog/status');assert status==200 and body['status']=='ready' and body['personalized'] is False
status,brands=fetch('/products/brands?limit=10');assert status==200 and brands and all(r['brand'] and r['count']>0 for r in brands)
status,product=fetch('/products/'+quote(sys.argv[1],safe=''));assert status==200 and product['id']==sys.argv[1] and Decimal(str(product['price']))==Decimal(sys.argv[2])
checks={}
for path in ('/feed?limit=1','/catalog/discovery?limit=1','/profile'):
    status,_=fetch(path);assert status==401;checks[path.split('?')[0]]=status
status,_=fetch('/visual-analysis','POST');assert status==503
print(json.dumps({'status':'passed','api_version':'1.0.1-build15','catalogue_status':'ready','brands':len(brands),'real_sku_price_exact':True,'unauthenticated':checks,'visual_disabled_status':503,'production_user_fixtures_created':False}))
'''
    raw = command(label, [*BASE.DOCKER, 'exec', '-i', item['id'], 'python', '-', sku, price],
                  data=source.encode(), timeout=110)
    result = json.loads(raw)
    require(result.get('status') == 'passed' and result.get('api_version') == '1.0.1-build15'
            and result.get('real_sku_price_exact') is True, 'private_http_smoke_required')
    return result


def port_map(values):
    result = set()
    for value in values or []:
        require(isinstance(value, dict) and 'target' in value and 'published' in value, 'explicit_rendered_ports_required')
        ip = value.get('host_ip') or '0.0.0.0'
        result.add((str(value['target']) + '/' + value.get('protocol', 'tcp'), ip, str(value['published'])))
    return result


def runtime_ports(obj):
    return {(port, bind.get('HostIp') or '0.0.0.0', bind['HostPort'])
        for port, bindings in (obj['HostConfig'].get('PortBindings') or {}).items() for bind in bindings}


def compose_argv():
    return [*BASE.DOCKER, 'compose', '--project-name', 'modish', '--project-directory', '/opt/Modish',
            '-f', str(ROOT / 'current-compose.private.yml'), '-f', str(ROOT / 'candidate.compose.private.json')]


def validate_rendered(configuration, old, environment):
    service = configuration['services']['backend']
    require(service.get('image') == IMAGE and service.get('entrypoint') == [] and service.get('command') == COMMAND
            and service.get('environment') == environment, 'exact_rendered_candidate_runtime_required')
    require(port_map(service.get('ports')) == runtime_ports(old), 'rendered_ports_changed')
    rendered_networks = {configuration['networks'][key]['name'] for key in service.get('networks', {})}
    require(rendered_networks == set(old['NetworkSettings']['Networks']) == {'modish_default'}, 'rendered_networks_changed')
    mounts = set()
    for value in service.get('volumes', []):
        require(isinstance(value, dict), 'resolved_mount_objects_required')
        source = value['source']
        if value['type'] == 'volume':
            source = configuration['volumes'][source]['name']
        mounts.add((value['type'], source, value['target'], not value.get('read_only', False)))
    old_mounts = {(value['Type'], value.get('Name') if value['Type'] == 'volume' else value['Source'],
                   value['Destination'], value['RW']) for value in old['Mounts']}
    require(mounts == old_mounts, 'rendered_storage_mounts_changed')
    require(service.get('privileged', False) is False and service.get('network_mode') not in {'host', 'container'},
            'unsafe_rendered_runtime_forbidden')
    require(not service.get('post_start') and not service.get('pre_stop')
            and service.get('scale', 1) == 1 and service.get('deploy', {}).get('replicas', 1) == 1,
            'compose_lifecycle_hooks_or_extra_replicas_forbidden')


def compose_literals(value):
    """A resolved Compose config must not interpolate credentials a second time."""
    if isinstance(value, str):
        return value.replace('$', '$$')
    if isinstance(value, list):
        return [compose_literals(item) for item in value]
    if isinstance(value, dict):
        return {key: compose_literals(item) for key, item in value.items()}
    return value


def main():
    global BASE, UTIL, PLAN, ROOT, STATE, ARGS, LOCK
    global MIGRATION_ATTEMPTED, IMPORT_ATTEMPTED, IMPORT_COMMITTED, PUBLIC_OPEN_ATTEMPTED
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('root', 'plan-id', 'plan-sha256', 'final-backup-report-sha256', 'proxy-plan-sha256',
                 'render-id', 'render-report-sha256', 'approval-token'):
        parser.add_argument('--' + name, required=True)
    ARGS = args = parser.parse_args()
    require(os.name == 'posix' and os.geteuid() == 0, 'linux_root_required')
    require(args.approval_token == APPROVAL and all(re.fullmatch(r'[0-9a-f]{64}', value) for value in
        (args.plan_sha256, args.final_backup_report_sha256, args.proxy_plan_sha256, args.render_report_sha256)),
        'explicit_approval_and_pinned_reports_required')
    require(re.fullmatch(r'[0-9a-f]{32}', args.plan_id), 'uuid_plan_required')
    ROOT = Path(args.root)
    require(ROOT == Path('/opt/modish-releases/20261022-build15-activation-plan-' + args.plan_id)
            and ROOT.is_dir() and not ROOT.is_symlink() and ROOT.resolve() == ROOT
            and ROOT.stat().st_uid == 0 and ROOT.stat().st_mode & 0o777 == 0o700,
            'prepared_private_root_required')
    raw = private(ROOT / 'activation-plan.json')
    require(hashlib.sha256(raw).hexdigest() == args.plan_sha256, 'external_plan_hash_required')
    PLAN = json.loads(raw)
    require(PLAN.get('plan_id') == args.plan_id and PLAN.get('run_id') == RUN and PLAN.get('image_id') == IMAGE
            and PLAN.get('archive_sha256') == ARCHIVE and PLAN.get('snapshot_manifest_sha256') == MANIFEST
            and PLAN.get('source_revision') == '20261013_0025' and PLAN.get('target_revision') == '20261015_0028'
            and PLAN.get('approval_token') == APPROVAL and PLAN.get('approval_required') is True,
            'exact_reviewed_plan_required')
    previous = ROOT / 'final-backup-attempt'
    backup_raw = private(previous / 'final-backup-report.json')
    require(hashlib.sha256(backup_raw).hexdigest() == args.final_backup_report_sha256, 'pinned_final_backup_report_required')
    backup = json.loads(backup_raw)
    require(backup.get('status') == 'maintenance_final_backup_restore_verified'
            and backup.get('plan_id') == args.plan_id and backup.get('plan_sha256') == args.plan_sha256
            and backup.get('restored_full_rows_preserved') is True and backup.get('source_rows_unchanged') is True
            and backup.get('proxy_plan_sha256') == args.proxy_plan_sha256
            and backup.get('backend_auto_restart_disabled') is True
            and backup.get('backend_stopped') is True and backup.get('writes_open') is False
            and backup.get('production_schema_migrated') is False and backup.get('production_import_started') is False,
            'closed_writers_and_verified_final_backup_required')
    dump = previous / 'final-production.dump'
    require(dump.is_file() and not dump.is_symlink() and sha(dump) == backup['dump_sha256'], 'verified_final_dump_unchanged_required')
    for name, field in (('candidate.docker-env.private', 'candidate_environment_sha256'),
                        ('candidate.compose.private.json', 'candidate_override_sha256'),
                        ('current-runtime.private.json', 'current_runtime_sha256'),
                        ('current-compose.private.yml', 'current_compose_sha256')):
        private(ROOT / name, 1024 * 1024)
        require(sha(ROOT / name) == PLAN[field], 'runtime_artifact_hash_required')
    require(sha(Path('/opt/Modish/docker-compose.yml')) == PLAN['current_compose_sha256'], 'live_compose_changed')
    environment = {}
    for line in private(ROOT / 'candidate.docker-env.private').decode().splitlines():
        key, value = line.split('=', 1)
        require(key not in environment, 'unique_candidate_environment_required')
        environment[key] = value
    require(all(environment.get(key) == value for key, value in POLICY.items())
            and hashlib.sha256(environment['DATABASE_URL'].encode()).hexdigest() == PLAN.get('api_database_url_sha256')
            and PLAN.get('preserve_api_database_credentials') is True and len(environment.get('JWT_SECRET', '')) >= 32,
            'preserved_public_production_environment_required')
    old = json.loads(private(ROOT / 'current-runtime.private.json', 1024 * 1024))
    require(environment['JWT_SECRET'] == dict(value.split('=', 1) for value in old['Config']['Env'])['JWT_SECRET'],
            'existing_api_jwt_must_be_preserved')
    importer = ROOT / 'catalog_refresh15_production_import.py'
    require(importer.is_file() and not importer.is_symlink() and sha(importer) == IMPORTER_SHA, 'reviewed_production_importer_required')
    worker_contract = load(importer, IMPORTER_SHA, 'reviewed_production_worker_contract')
    worker_contract.validate_plan_value(PLAN)
    rehearsal = Path('/opt/modish-releases/20261021-catalog-refresh15-' + RUN)
    feeds = rehearsal / 'feeds'
    require(feeds.is_dir() and not feeds.is_symlink() and sha(feeds / 'download-manifest.json') == MANIFEST,
            'rehearsed_manifest_required')
    manifest = json.loads(private(feeds / 'download-manifest.json', 16384))
    require(manifest.get('run_id') == RUN and {item['code'] for item in manifest['sources']} == set(FEEDS),
            'fixed_source_manifest_required')
    now = datetime.now(timezone.utc)
    for item in manifest['sources']:
        stamp = datetime.fromisoformat(item['observed_at'])
        require(stamp.tzinfo is not None and now - timedelta(hours=72) <= stamp <= now + timedelta(minutes=5)
                and item['feed_id'] == FEEDS[item['code']] and item['filename'] == item['code'] + '.csv',
                'current_source_observation_required')
        path = feeds / item['filename']
        require(path.is_file() and not path.is_symlink() and path.stat().st_size == item['bytes']
                and sha(path) == item['sha256'], 'source_snapshot_hash_required')
    STATE = ROOT / 'activation-continuation'
    require(not STATE.exists(), 'existing_continuation_forbids_replay')
    UTIL = load(ROOT / 'build15_activation_final_backup_remote.py', BACKUP_HELPER_SHA, 'build15_final_backup_utilities')
    proxy = load(ROOT / 'build15_proxy_maintenance_remote.py', PROXY_HELPER_SHA, 'build15_proxy_maintenance_utilities')
    proxy_plan = proxy.proposal(ROOT, args.proxy_plan_sha256, args.plan_sha256)
    require(re.fullmatch(r'[0-9a-f]{32}', args.render_id), 'prepared_render_uuid_required')
    render_stage = ROOT / ('render-review-' + args.render_id)
    require(render_stage.is_dir() and not render_stage.is_symlink() and render_stage.resolve() == render_stage
            and render_stage.stat().st_uid == 0 and render_stage.stat().st_mode & 0o777 == 0o700,
            'private_preapproval_render_stage_required')
    render_raw = private(render_stage / 'render-report.json')
    require(hashlib.sha256(render_raw).hexdigest() == args.render_report_sha256, 'pinned_preapproval_render_report_required')
    render_report = json.loads(render_raw)
    require(render_report.get('operation') == 'build15_readonly_resolved_runtime_proposal'
            and render_report.get('status') == 'prepared_for_review' and render_report.get('render_id') == args.render_id
            and render_report.get('plan_sha256') == args.plan_sha256
            and render_report.get('proxy_plan_sha256') == args.proxy_plan_sha256
            and render_report.get('continuation_sha256') == sha(Path(__file__))
            and render_report.get('image_id') == IMAGE and render_report.get('baseline_runtime_continuity_verified') is True
            and render_report.get('candidate_environment_sha256') == PLAN['candidate_environment_sha256']
            and render_report.get('current_runtime_sha256') == PLAN['current_runtime_sha256']
            and render_report.get('current_compose_sha256') == PLAN['current_compose_sha256']
            and render_report.get('public_api_host_ip') == '127.0.0.1' and render_report.get('public_api_port') == 8000,
            'exact_reviewed_runtime_render_required')
    rendered_bytes = private(render_stage / 'rendered-original-ports.private.json', 1024*1024)
    prepared_execution = private(render_stage / 'candidate-execution.compose.private.json', 1024*1024)
    require(hashlib.sha256(rendered_bytes).hexdigest() == render_report['raw_render_sha256']
            and hashlib.sha256(prepared_execution).hexdigest() == render_report['execution_compose_sha256'],
            'preapproval_compose_artifacts_unchanged_required')
    fence_raw = private(previous / 'proxy-fence-report.json')
    require(hashlib.sha256(fence_raw).hexdigest() == backup['proxy_fence_report_sha256'], 'pinned_proxy_fence_report_required')
    fence = json.loads(fence_raw)
    BASE = load(rehearsal / 'catalog_refresh15_clone_remote.py', BASE_SHA, 'build15_command_utilities')
    LOCK = os.open('/opt/modish-releases/build15-activation.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    require(os.fstat(LOCK).st_uid == 0 and os.fstat(LOCK).st_mode & 0o777 == 0o600, 'private_activation_lock_required')
    fcntl.flock(LOCK, fcntl.LOCK_EX | fcntl.LOCK_NB)
    os.umask(0o077)
    STATE.mkdir(mode=0o700)
    proxy.syncdir(ROOT)
    BASE.R = STATE
    client = STATE / 'docker-client'; client.mkdir(mode=0o700); BASE.json_file(client / 'config.json', {})
    BASE.DOCKER.extend(['--config', str(client)])
    UTIL.BASE, UTIL.ARGS, UTIL.ROOT, UTIL.STATE = BASE, args, ROOT, STATE
    UTIL.DEADLINE = monotonic() + 14400
    phase('preflight', explicit_owner_approval_marker=True, writes_open=False)
    old_stopped('continuation_old_stopped')
    proxy.assert_fenced(BASE, ROOT, args.proxy_plan_sha256, args.plan_sha256, fence)
    UTIL.assert_no_other_sessions()
    require(UTIL.readonly_sql('SELECT version_num FROM alembic_version;') == '20261013_0025', 'source0025_required')
    require(UTIL.source_integrity() == json.loads(private(previous / 'source-integrity-before.json')),
            'production_changed_after_final_backup')
    canonical = table_proof(); record('migration-integrity-before', canonical)
    image = json.loads(command('candidate_image', [*BASE.DOCKER, 'image', 'inspect', IMAGE]))[0]
    BASE.require_image(image, IMAGE, RUN)
    require(image['Config'].get('Entrypoint') in ([], None) and image['Config'].get('Cmd') == COMMAND,
            'direct_uvicorn_image_required')
    expected_public_environment = dict(value.split('=', 1) for value in image['Config'].get('Env', []))
    expected_public_environment.update(environment)
    # Consume the exact config rendered/reviewed BEFORE owner approval. Never
    # discover Compose merges or interpolation drift under the writers fence.
    rendered = json.loads(rendered_bytes)
    validate_rendered(rendered, old, environment)
    with (STATE / 'rendered-compose.private.json').open('xb') as stream:
        os.fchmod(stream.fileno(), 0o600); stream.write(rendered_bytes); stream.flush(); os.fsync(stream.fileno())
    # Compose's ordinary port-list merge would retain the old public binding.
    # Replace it in the FULL resolved config, not in an appended override list.
    rendered['services']['backend']['ports'] = [proxy_plan['public_api_binding']]
    desired_ports = {('8000/tcp', '127.0.0.1', '8000')}
    require(port_map(rendered['services']['backend']['ports']) == desired_ports, 'loopback_only_api_binding_required')
    execution_compose = STATE / 'resolved-execution.compose.private.json'
    require(json.loads(prepared_execution) == compose_literals(rendered), 'reviewed_full_config_port_replacement_required')
    with execution_compose.open('xb') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(prepared_execution)
        stream.flush(); os.fsync(stream.fileno())
    execution_compose_sha = sha(execution_compose)
    phase('rendered_runtime_verified', old_ports_and_storage_preserved=True)
    worker_env = {'DATABASE_URL': environment['DATABASE_URL'], 'JWT_SECRET': WORKER_JWT,
        'APP_ENV': 'production', 'PYTHON_DOTENV_DISABLED': '1', 'PYTHONDONTWRITEBYTECODE': '1',
        'ADMITAD_ENABLED': 'false', 'STYLE_VISUAL_ENABLED': 'false', 'CLOSED_ALPHA_ENABLED': 'false',
        'ALLOW_DEV_ANALYZE': 'false'}
    migration = create_worker('migration', ['-m', 'alembic', 'upgrade', '20261015_0028'],
        {**worker_env, 'PGOPTIONS': '-c statement_timeout=120000 -c lock_timeout=5000'}, memory='1024m')
    old_stopped('before_migration_old_stopped'); UTIL.assert_no_other_sessions()
    proxy.assert_fenced(BASE, ROOT, args.proxy_plan_sha256, args.plan_sha256, fence)
    MIGRATION_ATTEMPTED = True
    execute_worker(migration, 600)
    schema0028()
    require(table_proof() == canonical, 'normalized_migration_full_rows_changed')
    phase('migration_verified', source0025_to0028=True, normalized_full_rows_preserved=True)
    outcome = STATE / 'production-import-outcome'; outcome.mkdir(mode=0o700)
    import_env = {**worker_env, 'REFRESH_RUN_ID': RUN, 'AUDIT_IMAGE_ID': IMAGE,
        'AUDIT_ARCHIVE_SHA256': ARCHIVE, 'REFRESH_MANIFEST_SHA256': MANIFEST,
        'PLAN_SHA256': args.plan_sha256, 'PRODUCTION_APPROVAL_TOKEN': APPROVAL}
    imported = create_worker('import', ['/run.py'], import_env,
        [(importer, '/run.py', False), (ROOT / 'activation-plan.json', '/plan/activation-plan.json', False),
         (feeds, '/feeds', False), (outcome, '/outcome', True)])
    old_stopped('before_import_old_stopped'); UTIL.assert_no_other_sessions()
    IMPORT_ATTEMPTED = True
    output = execute_worker(imported, 7200)
    result = json.loads(private(output, 4*1024*1024))
    committed = json.loads(private(outcome / 'committed.json', 131072))
    require(result == committed and result.get('result') == 'production_import_committed'
            and result.get('transaction_state') == 'committed' and result.get('needs_review') is False
            and result.get('plan_sha256') == args.plan_sha256 and result.get('snapshot_manifest_sha256') == MANIFEST
            and result.get('existing_product_ids_preserved') is True and result.get('unaffected_rows_preserved') is True
            and len(result.get('sources', [])) == 4
            and {item['code']: item['rows'] for item in result['sources']} == ROWS,
            'atomic_production_import_committed_proof_required')
    IMPORT_COMMITTED = True
    clocks = json.loads(UTIL.readonly_sql("SELECT json_object_agg(code,last_sync_at) FROM product_sources WHERE "
        "code IN ('baon','shoppinglive','serginnetti','postmeridiem');"))
    require(all(datetime.fromisoformat(clocks[item['code']]) == datetime.fromisoformat(item['observed_at'])
                for item in manifest['sources']), 'persisted_real_observation_times_required')
    schema0028()
    phase('atomic_import_verified', sources=4, input_rows=sum(ROWS.values()), existing_ids_preserved=True)
    stock = json.loads(UTIL.readonly_sql("SELECT json_build_object('count',count(*),'id',(array_agg(id ORDER BY "
        "CASE WHEN price<>trunc(price) THEN 0 ELSE 1 END,id))[1],'price',(array_agg(price::text ORDER BY "
        "CASE WHEN price<>trunc(price) THEN 0 ELSE 1 END,id))[1]) FROM products WHERE is_active=1 AND is_available=1 "
        "AND is_deleted_from_feed=0 AND source IN ('baon','shoppinglive','serginnetti','postmeridiem') "
        "AND availability_status IN ('in_stock','available') AND last_seen_in_feed_at>=now()-interval '72 hours' "
        "AND last_seen_in_feed_at<=now()+interval '5 minutes';"))
    require(stock['count'] > 0 and isinstance(stock['id'], str) and re.fullmatch(r'[A-Za-z0-9_-]{1,80}', stock['id']),
            'fresh_actual_stock_and_sku_required')
    # Private API has no published ports and no backend Compose label/alias.
    # A read-only DB session protects against accidental writes during smoke.
    shadow = create_worker('private_api', COMMAND[1:],
        {**environment, 'PGOPTIONS': '-c default_transaction_read_only=on -c statement_timeout=30000 -c lock_timeout=3000'})
    phase('private_api_about_to_start', no_published_ports=True)
    command('start_private_api', [*BASE.DOCKER, 'start', shadow['id']])
    owned(BASE.inspect(shadow['id'], 'private_api_running'), shadow)
    smoke_before = table_proof()
    smoke = private_http_smoke(shadow, stock['id'], stock['price'])
    require(table_proof() == smoke_before, 'private_smoke_modified_database_rows')
    command('stop_private_api', [*BASE.DOCKER, 'stop', '--time', '15', shadow['id']], timeout=30)
    require(not owned(BASE.inspect(shadow['id'], 'private_api_stopped'), shadow)['State']['Running'],
            'private_api_must_stop_before_public_open')
    UTIL.assert_no_other_sessions(); old_stopped('before_public_recreate_old_stopped')
    phase('private_smoke_passed', **smoke)
    # Recreate only backend; PG/Redis/volumes are never recreated. --no-start
    # preserves the writes fence while inspecting the final public runtime.
    phase('public_container_create_about_to_start', writes_open=False)
    require(sha(execution_compose) == execution_compose_sha, 'verified_resolved_compose_unchanged_required')
    command('compose_create_backend', [*BASE.DOCKER, 'compose', '--project-name', 'modish',
        '--project-directory', '/opt/Modish', '-f', str(execution_compose),
        'up', '--no-start', '--no-deps', '--no-build', 'backend'], timeout=120)
    new = BASE.inspect('modish_backend', 'new_public_backend_stopped')
    require(new['Id'] != BASE.PROD['modish_backend'][0] and new['Image'] == IMAGE and not new['State']['Running']
            and new['Config'].get('Entrypoint') in ([], None) and new['Config'].get('Cmd') == COMMAND
            and dict(value.split('=', 1) for value in new['Config']['Env']) == expected_public_environment
            and runtime_ports(new) == desired_ports
            and {(m['Type'],m['Source'],m['Destination'],m['RW']) for m in new['Mounts']}
                == {(m['Type'],m['Source'],m['Destination'],m['RW']) for m in old['Mounts']}
            and set(new['NetworkSettings']['Networks']) == {'modish_default'}, 'stopped_public_runtime_must_match_review')
    labels = new['Config'].get('Labels') or {}
    require(labels.get('com.docker.compose.project') == 'modish' and labels.get('com.docker.compose.service') == 'backend',
            'public_backend_compose_identity_required')
    schema0028(); UTIL.assert_no_other_sessions()
    proxy.assert_fenced(BASE, ROOT, args.proxy_plan_sha256, args.plan_sha256, fence)
    phase('loopback_api_about_to_start', new_backend_id=new['Id'], writes_open=False)
    command('start_fenced_loopback_backend', [*BASE.DOCKER, 'start', new['Id']], timeout=60)
    public = BASE.inspect(new['Id'], 'public_backend_running')
    require(public['Id'] == new['Id'] and public['Image'] == IMAGE and public['State']['Running'], 'public_backend_running_required')
    # No writeful fixture/auth journey in production; that was proved on copy.
    post = private_http_smoke({'id': new['Id']}, stock['id'], stock['price'], label='public_http_smoke')
    proxy.assert_fenced(BASE, ROOT, args.proxy_plan_sha256, args.plan_sha256, fence)
    phase('writes_open_about_to_start', new_backend_id=new['Id'], public_writes_may_exist_after_this_marker=True)
    PUBLIC_OPEN_ATTEMPTED = True
    proxy.open_after_smoke(BASE, STATE, ROOT, args.proxy_plan_sha256, args.plan_sha256, fence, args.approval_token)
    report = {'operation': 'build15_approved_production_activation', 'status': 'activated_and_smoke_verified',
        'plan_id': args.plan_id, 'plan_sha256': args.plan_sha256, 'run_id': RUN, 'image_id': IMAGE,
        'archive_sha256': ARCHIVE, 'manifest_sha256': MANIFEST, 'schema': '20261015_0028',
        'final_backup_report_sha256': args.final_backup_report_sha256,
        'production_importer_sha256': IMPORTER_SHA, 'sources': 4, 'input_rows': sum(ROWS.values()),
        'executed_compose_sha256': execution_compose_sha,
        'preapproval_render_report_sha256': args.render_report_sha256,
        'proxy_plan_sha256': args.proxy_plan_sha256, 'api_published_only_to_loopback': True,
        'backend_id': new['Id'], 'api_version': post['api_version'], 'writes_open': True,
        'jwt_preserved': True, 'timer_installed': False, 'automatic_old_api_rollback_allowed': False,
        'public_android_signing_ready': False, 'finished_at': datetime.now(timezone.utc).isoformat()}
    record('activation-report', report)
    print(json.dumps(report))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        cleanup_review = cleanup_owned_workers() if BASE is not None else False
        failure = {'operation': 'build15_approved_production_activation', 'status': 'failed', 'phase': PHASE,
            'error_type': type(error).__name__, 'raw_error_omitted': True, 'requires_review': True,
            'schema_migration_attempted': MIGRATION_ATTEMPTED, 'import_attempted': IMPORT_ATTEMPTED,
            'import_committed_verified': IMPORT_COMMITTED,
            'import_outcome_uncertain_requires_review': IMPORT_ATTEMPTED and not IMPORT_COMMITTED,
            'public_writes_may_exist': PUBLIC_OPEN_ATTEMPTED, 'owned_cleanup_requires_review': cleanup_review,
            'automatic_retry': False, 'automatic_restore_or_old_api_start': False, 'artifacts_preserved': True}
        if type(error) is RuntimeError and re.fullmatch(r'[a-z][a-z0-9_]{0,100}', str(error)):
            failure['check'] = str(error)
        if BASE is not None and STATE is not None and STATE.is_dir():
            try:
                record('activation-failed', failure)
            except Exception:
                failure['ledger_write_requires_review'] = True
        print(json.dumps(failure))
        raise SystemExit(1) from None
    finally:
        if LOCK is not None:
            os.close(LOCK)
