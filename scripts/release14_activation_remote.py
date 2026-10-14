"""Plan, activate or safely roll back build14; intended for reviewed VPS use only.

PLAN reads production state, builds/tests a guarded BASE image offline and writes
a reviewable manifest. ACTIVATE additionally needs the exact plan hash and an
explicit --production-import-approved flag. ROLLBACK changes only the API image;
it never restores a database, downgrades migrations or reruns a catalogue import.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from uuid import uuid4

R = Path('/opt/modish-releases/20261014-build14')
PRIVATE = R / 'activation-private-v2'
COMPOSE = Path('/opt/Modish/docker-compose.yml')
LEGACY_OVERRIDE = Path('/opt/modish-releases/20261006-build13/release.compose.json')
LEGACY_COMPOSE_FILES = (COMPOSE, LEGACY_OVERRIDE)
ENVFILE = Path('/opt/Modish/backend/.env')
PROJECT, SERVICE, NETWORK = 'modish', 'backend', 'modish_default'
BASE = 'sha256:4ae234fc8a1f8082bb368f4f617d187a6138bb5848c02af5e9a7a9f15a3d6d7e'
IMAGE = 'sha256:eeea30209dcaa366fd936b6181ce84d47fc475a342df257893d58bb725dac1ec'
ARCHIVE = '04d9ec34fb32e45823f2b19397e0fd1a0f21d85dd45a487ad0914c7313ad1424'
BOUNDED_IMPORT = 'c366b746ecc9f4df264d3a1266fd9cd82e4ea30a5c5a01f924ddc1325a18436e'
PRODUCTION_IMPORT = 'e502bca7375c52e6a10d2c08f1a19cfeeab9bdc4f3a84270f3ecfa7328583b0d'
OLD_REVISION, REVISION = '20260917_0021', '20261013_0025'
# The pinned build13 image advertises alpha in both health and OpenAPI. Its
# release identity is the immutable Docker hash, not an invented API version.
BASE_VERSION, VERSION = '1.0.0-alpha', '1.0.0-build14'
FEEDS = {'baon': '19982', 'shoppinglive': '25461', 'serginnetti': '26417', 'postmeridiem': '25200'}
PROD_NAMES = ('modish_backend', 'modish_postgres', 'modish_redis')
BASE_URLS = ('http://127.0.0.1:8000', 'https://modish.org.ru')
APPROVAL = 'build14-four-reviewed-snapshots'


class GuardError(RuntimeError):
    pass


def require(condition, code):
    if not condition:
        raise GuardError(code)


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def regular(path, expected=None, limit=4 * 1024 * 1024):
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= limit, 'regular_bounded_file_required')
    if expected is not None:
        require(sha(path) == expected, 'pinned_file_changed')
    return path


def capture(*command, **kwargs):
    return subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs).stdout


def inspect(name):
    return json.loads(capture('docker', 'inspect', name))[0]


def write_json(path, data, *, replace=False):
    if replace:
        require(not path.is_symlink(), 'report_symlink')
        temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
        write_json(temporary, data)
        os.replace(temporary, path)
    else:
        with path.open('x', encoding='utf-8') as stream:
            json.dump(data, stream, indent=2)
            stream.write('\n')


def progress(record, phase):
    record.update(phase=phase, updated_at=datetime.now(timezone.utc).isoformat())
    write_json(R / 'activation-progress.json', record, replace=True)
    print(json.dumps({'status': record['status'], 'phase': phase}), flush=True)


def snapshot():
    result = {}
    for name in PROD_NAMES:
        obj = inspect(name)
        result[name] = {'id': obj['Id'], 'image': obj['Image'], 'running': obj['State']['Running'],
                        'started_at': obj['State']['StartedAt'], 'restart_count': obj['RestartCount']}
    return result


def query(sql):
    return capture('docker', 'exec', '-e',
                   'PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=120000 -c lock_timeout=5000',
                   'modish_postgres', 'psql', '-X', '-qAt', '-U', 'modish_user', '-d', 'modish',
                   '--set', 'ON_ERROR_STOP=1', '-c', sql, text=True).strip()


def infrastructure_unchanged(plan):
    current = snapshot()
    require(all(current[name] == plan['production_before'][name] for name in ('modish_postgres', 'modish_redis')),
            'production_database_or_redis_container_changed')
    require(query('SELECT current_database() || \':\' || current_user') == 'modish:modish_user', 'production_database_identity')


def production_scope():
    require(R.is_dir() and R.resolve() == R and not R.is_symlink(), 'fixed_release_directory_required')
    regular(COMPOSE)
    regular(ENVFILE)
    backend = inspect('modish_backend')
    labels = backend['Config']['Labels']
    require(labels.get('com.docker.compose.project') == PROJECT and labels.get('com.docker.compose.service') == SERVICE and
            labels.get('com.docker.compose.project.working_dir') == '/opt/Modish', 'compose_project_or_service_mismatch')
    config_files = tuple(labels.get('com.docker.compose.project.config_files', '').split(','))
    allowed = tuple(str(path) for path in LEGACY_COMPOSE_FILES) if backend['Image'] == BASE else (str(COMPOSE),)
    require(config_files == allowed, 'compose_config_identity')
    for path in config_files:
        regular(Path(path))
    # Read an already-running app's DB setting only in memory; never log it.
    values = dict(item.split('=', 1) for item in backend['Config']['Env'] if '=' in item)
    effective = json.loads(capture(*compose_command(tuple(Path(path) for path in config_files)),
                                   'config', '--format', 'json'))
    configured = effective['services'][SERVICE]
    require(inspect(configured['image'])['Id'] == backend['Image'], 'live_compose_image_drift')
    require(all(value is None or values.get(key) == str(value) for key, value in
                configured.get('environment', {}).items()), 'live_compose_environment_drift')
    url = urlsplit(values['DATABASE_URL'].replace('postgresql+psycopg://', 'postgresql://'))
    require(url.scheme == 'postgresql' and url.hostname == 'postgres' and url.path == '/modish' and
            url.username == 'modish_user' and url.port in (None, 5432) and not url.query and not url.fragment,
            'production_url_identity')
    require(set(backend['NetworkSettings']['Networks']) == {NETWORK}, 'production_api_network_mismatch')
    for name in ('modish_postgres', 'modish_redis'):
        obj = inspect(name)
        require(obj['State']['Running'] and NETWORK in obj['NetworkSettings']['Networks'], 'production_dependency_network')
    require('postgres' in inspect('modish_postgres')['NetworkSettings']['Networks'][NETWORK]['Aliases'], 'postgres_network_alias')
    require(query('SELECT current_database() || \':\' || current_user') == 'modish:modish_user', 'production_database_identity')
    return values['DATABASE_URL']


def compose_command(path=COMPOSE):
    paths = path if isinstance(path, (tuple, list)) else (path,)
    command = ['docker', 'compose', '-p', PROJECT, '--project-directory', '/opt/Modish', '--env-file', str(ENVFILE)]
    for source in paths:
        command += ['-f', str(source)]
    return command


def backend_execution(source, image, module):
    """Strict indentation edit; Docker Compose validates the full result later."""
    require(re.fullmatch(r'sha256:[0-9a-f]{64}', image) and module in ('app.main:app', 'release14_rollback_guard:app'),
            'backend_execution_values')
    lines = source.splitlines(keepends=True)
    services = [i for i, line in enumerate(lines) if re.fullmatch(r'services:\s*(?:#.*)?\n?', line)]
    require(len(services) == 1, 'one_top_level_services_mapping_required')
    start = services[0] + 1
    end = next((i for i in range(start, len(lines)) if re.match(r'^\S', lines[i]) and not lines[i].startswith('#')), len(lines))
    indexes = [i for i in range(start, end) if re.fullmatch(r'  backend:\s*(?:#.*)?\n?', lines[i])]
    require(len(indexes) == 1, 'one_two_space_backend_mapping_required')
    begin = indexes[0] + 1
    finish = next((i for i in range(begin, end) if re.match(r'^  \S', lines[i]) and not lines[i].lstrip().startswith('#')), end)
    remove = {'image', 'entrypoint', 'command', 'build', 'pull_policy', 'stop_grace_period'}
    kept, skip = [], False
    for line in lines[begin:finish]:
        field = re.match(r'^    ([A-Za-z_][A-Za-z0-9_]*):', line)
        if field:
            skip = field[1] in remove
        if not skip:
            kept.append(line)
    generated = ['    image: ' + image + '\n', '    pull_policy: never\n',
                 '    entrypoint: ["python", "-m", "uvicorn"]\n',
                 '    command: [' + json.dumps(module) + ', "--host", "0.0.0.0", "--port", "8000"]\n',
                 '    stop_grace_period: 240s\n']
    return ''.join(lines[:begin] + generated + kept + lines[finish:])


def validate_compose_variant(path, image, module):
    # Compare against the complete ordered live launch configuration. The old
    # build13 override must never silently disappear along with its settings.
    old = json.loads(capture(*compose_command(LEGACY_COMPOSE_FILES), 'config', '--format', 'json'))
    new = json.loads(capture(*compose_command(path), 'config', '--format', 'json'))
    service = new['services'][SERVICE]
    require(service['image'] == image and service['entrypoint'] == ['python', '-m', 'uvicorn'] and
            service['command'] == [module, '--host', '0.0.0.0', '--port', '8000'] and
            service['pull_policy'] == 'never' and 'build' not in service, 'compose_backend_execution_mismatch')
    for config in (old, new):
        for key in ('image', 'entrypoint', 'command', 'build', 'pull_policy', 'stop_grace_period'):
            config['services'][SERVICE].pop(key, None)
    require(old == new, 'compose_changes_outside_backend_execution')


def evidence(report):
    require(report['status'] == 'prepared_ready_for_review' and report['phase'] == 'isolated_checks_complete' and
            report['ready_for_activation'] is False and report['archive_sha256'] == ARCHIVE and report['image_id'] == IMAGE and
            report['bounded_import_sha256'] == BOUNDED_IMPORT and report['outside_sources_unchanged'] is True and
            report['production_metadata_unchanged'] is True and report['production_database_writes'] is False,
            'completed_bounded_evidence_required')
    require(report['unit_tests']['status'] == 'passed' and report['unit_tests']['count'] == 435, 'candidate_435_units_required')
    photo = report['photo_postgres']
    require(photo['status'] == 'passed' and photo['revision'] == REVISION and photo['fixture_cleanup'] == 'committed' and
            photo['external_provider_calls'] == 0 and len(photo['checks']) == 5 and
            all(check['status'] == 'passed' for check in photo['checks'].values()), 'photo_postgres_five_checks_required')
    for key in ('onboarding_one_remaining_slot', 'style_profile_one_remaining_slot', 'onboarding_duplicate_key', 'style_profile_duplicate_key'):
        check = photo['checks'][key]
        require(check['provider_calls'] == 1 and check['checked_out_while_blocked'] == 0 and check['replay_status'] == 200,
                'photo_concurrency_evidence_required')
    require(photo['checks']['same_email_registration']['http_statuses'] == [200, 409], 'registration_race_evidence_required')
    discovery = report['discovery_postgres']
    require(discovery['status'] == 'passed' and discovery['profile_count'] == 10 and discovery['fixture_cleanup'] == 'rolled_back' and
            discovery['users_restored'] and discovery['external_provider_calls'] == 0, 'ten_profile_discovery_required')
    http = report['discovery_http']
    require(http['status'] == 'passed' and http['fixture_cleanup'] == 'committed' and http['health_version'] == VERSION and
            http['capability_advertised'] and http['cold_strict_feed_status'] == 409 and http['external_provider_calls'] == 0 and
            {row['category'] for row in http['checks']} == {'shoes', 'outerwear'}, 'real_clone_http_smoke_required')
    imported = report['clone_import']
    require(imported['result'] == 'import_committed' and imported['single_transaction'] and
            imported['existing_product_ids_preserved'] and not imported['raw_timestamps_injected'] and
            set(imported['committed_sources']) == set(FEEDS), 'completed_atomic_clone_import_required')


def snapshots(report, *, check_age=True):
    manifest = json.loads(regular(R / 'feeds/download-manifest.json').read_text())
    require(manifest['result'] == 'download_complete' and manifest['scope'] == 'four_fixed_admitad_https_exports_no_database' and
            manifest['observation_clock_basis'] == 'real_download_request_start_utc' and
            len(manifest['sources']) == 4 and {row['code'] for row in manifest['sources']} == set(FEEDS), 'fixed_four_source_manifest')
    expected = {row['code']: row for row in report['clone_import']['sources']}
    require(set(expected) == set(FEEDS), 'clone_source_evidence_missing')
    now, result = datetime.now(timezone.utc), []
    for row in manifest['sources']:
        code = row['code']
        require(row['feed_id'] == FEEDS[code] and row['filename'] == code + '.csv', 'fixed_source_identity')
        path = regular(R / 'feeds' / row['filename'], row['sha256'], limit=256 * 1024 * 1024)
        require(path.stat().st_size == row['bytes'] > 0 and row['sha256'] == expected[code]['snapshot_sha256'], 'tested_snapshot_hash')
        observed, finished = (datetime.fromisoformat(row[key]) for key in ('observed_at', 'download_finished_at'))
        require(all(stamp.tzinfo is not None and stamp.utcoffset() == timedelta(0) for stamp in (observed, finished)) and
                observed <= finished <= now + timedelta(minutes=5) and
                observed == datetime.fromisoformat(expected[code]['observed_at']), 'actual_observation_provenance')
        if check_age:
            require(now - timedelta(hours=72) <= observed, 'snapshots_expired_new_approval_required')
        result.append({key: row[key] for key in ('code', 'feed_id', 'sha256', 'bytes', 'observed_at', 'download_finished_at')})
    return manifest, result


def log_run(command, name, *, timeout=1800, input=None):
    with (PRIVATE / name).open('xb') as log:
        subprocess.run(command, input=input, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=timeout)


def build_guard(guard_hash, tests_hash):
    regular(R / 'release14_rollback_guard.py', guard_hash)
    regular(R / 'release14_rollback_guard_tests.py', tests_hash)
    require(inspect(BASE)['Id'] == BASE, 'rollback_base_unavailable')
    context = PRIVATE / 'rollback-build'
    context.mkdir()
    shutil.copyfile(R / 'release14_rollback_guard.py', context / 'release14_rollback_guard.py')
    shutil.copyfile(R / 'release14_rollback_guard_tests.py', context / 'guard-tests.py')
    base_tag, tag = 'modish-backend:build14-rollback-base', 'modish-backend:build14-rollback-guard'
    capture('docker', 'tag', BASE, base_tag)
    with (context / 'Dockerfile').open('x') as stream:
        stream.write('FROM ' + base_tag + '\nWORKDIR /app\nCOPY release14_rollback_guard.py /app/release14_rollback_guard.py\n'
                     'COPY guard-tests.py /guard-tests.py\nENTRYPOINT ["python", "-m", "uvicorn"]\n'
                     'CMD ["release14_rollback_guard:app", "--host", "0.0.0.0", "--port", "8000"]\n')
    log_run(['docker', 'build', '--pull=false', '--network=none', '-t', tag, str(context)], 'rollback-build.log')
    require(inspect(base_tag)['Id'] == BASE, 'rollback_base_tag_changed')
    image = inspect(tag)['Id']
    env = ['-e', 'DATABASE_URL=sqlite://', '-e', 'JWT_SECRET=' + secrets.token_urlsafe(48),
           '-e', 'APP_ENV=test', '-e', 'PYTHON_DOTENV_DISABLED=1', '-e', 'PYTHONPATH=/app']
    log_run(['docker', 'run', '--rm', '--pull=never', '--network=none', *env, '--entrypoint', 'python', image,
             '/guard-tests.py'], 'rollback-guard-tests.log')
    probe = """from fastapi.testclient import TestClient
import hashlib
from pathlib import Path
import release14_rollback_guard
assert hashlib.sha256(Path('/app/release14_rollback_guard.py').read_bytes()).hexdigest()==GUARD_HASH
client=TestClient(release14_rollback_guard.app)
for path in ('/onboarding/photo','/onboarding/photo/','/style-profile/analyze','/style-profile/analyze/'):
 r=client.post(path,content=b'fixture')
 assert r.status_code==503 and r.headers.get('retry-after')
r=client.get('/health')
assert r.status_code==200 and r.json()['database']=='ok' and r.json()['version']==BASE_VERSION
assert client.get('/openapi.json').status_code==200
print('guarded_base_application_probe_passed')
""".replace('GUARD_HASH', repr(guard_hash)).replace('BASE_VERSION', repr(BASE_VERSION))
    log_run(['docker', 'run', '--rm', '--pull=never', '--network=none', *env, '--entrypoint', 'python', image,
             '-c', probe], 'rollback-base-probe.log')
    return image


def plan(args):
    production_scope()
    require(inspect('modish_backend')['Image'] == BASE and query('SELECT version_num FROM alembic_version') == OLD_REVISION,
            'expected_build13_schema21_required')
    regular(R / 'bounded-report.json', args.bounded_report_sha256)
    report = json.loads((R / 'bounded-report.json').read_text())
    evidence(report)
    require(snapshot() == report['production_before'], 'production_changed_since_clone_evidence')
    regular(R / 'source.tar', ARCHIVE, limit=256 * 1024 * 1024)
    regular(R / 'release14_import_bounded.py', BOUNDED_IMPORT)
    regular(R / 'release14_import_production.py', PRODUCTION_IMPORT)
    require(inspect(IMAGE)['Id'] == IMAGE, 'candidate_image_unavailable')
    _, feeds = snapshots(report)
    require(not PRIVATE.exists() and not (R / 'activation-plan.json').exists(), 'activation_plan_already_exists')
    PRIVATE.mkdir(mode=0o700)
    rollback_image = build_guard(args.rollback_guard_sha256, args.rollback_tests_sha256)
    original = COMPOSE.read_text()
    variants = {}
    for label, image, module in (('candidate', IMAGE, 'app.main:app'), ('rollback', rollback_image, 'release14_rollback_guard:app')):
        path = PRIVATE / (label + '.compose.yml')
        with path.open('x') as stream:
            stream.write(backend_execution(original, image, module))
        validate_compose_variant(path, image, module)
        variants[label] = {'path': str(path), 'sha256': sha(path), 'image': image, 'module': module}
    shutil.copyfile(COMPOSE, PRIVATE / 'original.compose.yml')
    require(snapshot() == report['production_before'], 'production_changed_during_plan')
    value = {'status': 'reviewable_activation_plan', 'created_at': datetime.now(timezone.utc).isoformat(),
             'archive_sha256': ARCHIVE, 'candidate_image': IMAGE, 'rollback_image': rollback_image,
             'script_sha256': sha(Path(__file__)), 'bounded_report_sha256': args.bounded_report_sha256,
             'bounded_import_sha256': BOUNDED_IMPORT, 'production_import_sha256': PRODUCTION_IMPORT,
             'rollback_guard_sha256': args.rollback_guard_sha256, 'rollback_tests_sha256': args.rollback_tests_sha256,
             'original_compose_sha256': sha(COMPOSE), 'production_environment_sha256': sha(ENVFILE),
             'original_compose_sources': [{'path': str(path), 'sha256': sha(path)} for path in LEGACY_COMPOSE_FILES],
             'production_before': report['production_before'], 'compose': variants, 'feeds': feeds,
             'verified': {'unit_tests': 435, 'photo_postgres_checks': 5, 'discovery_profiles': 10, 'discovery_http': 'passed',
                          'guarded_rollback_image': 'offline_tests_and_real_base_probe_passed'},
             'steps': ['fresh_consistent_readonly_pg_dump', 'guarded_base_api_and_old_worker_drain',
                       'additive_schema_0021_to_0025', 'atomic_four_source_import_preserving_observation_times',
                       'candidate_api_only_switch', 'local_and_public_health_openapi_unauthenticated_discovery'],
             'rollback': 'guarded_base_api_only_keep_schema_catalogue_photo_ledger_no_database_restore',
             'production_import_approval_required': True, 'production_changed': False,
             'catalogue_full_outfit_readiness': 'unproven'}
    write_json(R / 'activation-plan.json', value)
    print(json.dumps({'status': value['status'], 'plan_sha256': sha(R / 'activation-plan.json'),
                      'production_changed': False, 'approval_required': True}), flush=True)


def load_plan(expected):
    regular(R / 'activation-plan.json', expected)
    value = json.loads((R / 'activation-plan.json').read_text())
    require(value['status'] == 'reviewable_activation_plan' and value['script_sha256'] == sha(Path(__file__)) and
            value['candidate_image'] == IMAGE and value['archive_sha256'] == ARCHIVE and
            value['production_import_sha256'] == PRODUCTION_IMPORT, 'activation_plan_identity')
    regular(ENVFILE, value['production_environment_sha256'])
    require(tuple(row['path'] for row in value['original_compose_sources']) ==
            tuple(str(path) for path in LEGACY_COMPOSE_FILES), 'original_compose_source_order')
    # COMPOSE itself is replaced on activation; the legacy override remains
    # immutable evidence and is never used to override the new image.
    regular(LEGACY_OVERRIDE, value['original_compose_sources'][1]['sha256'])
    for variant in value['compose'].values():
        require(Path(variant['path']).parent == PRIVATE, 'compose_variant_scope')
        regular(Path(variant['path']), variant['sha256'])
        require(inspect(variant['image'])['Id'] == variant['image'], 'pinned_api_image_unavailable')
    infrastructure_unchanged(value)
    return value


def atomic_compose(content, allowed_hashes):
    require(sha(COMPOSE) in allowed_hashes and not COMPOSE.is_symlink(), 'live_compose_changed')
    temporary = COMPOSE.with_name('.release14-compose-' + uuid4().hex + '.tmp')
    with temporary.open('x') as stream:
        stream.write(content)
    os.replace(temporary, COMPOSE)


def switch_api(plan, kind, log_name):
    target = plan['compose'][kind]
    allowed = {plan['original_compose_sha256'], *(value['sha256'] for value in plan['compose'].values())}
    require(inspect('modish_backend')['Image'] in {BASE, IMAGE, plan['rollback_image']}, 'unrelated_api_image_running')
    atomic_compose(Path(target['path']).read_text(), allowed)
    log_run([*compose_command(), 'up', '-d', '--no-deps', '--no-build', '--pull', 'never', SERVICE], log_name, timeout=600)
    require(inspect('modish_backend')['Image'] == target['image'], 'compose_started_wrong_image')
    infrastructure_unchanged(plan)


def http_json(base, route, *, method='GET'):
    request = urllib.request.Request(base + route, method=method)
    # Default opener follows redirects, which could hide wrong public routes.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, request, fp, code, msg, headers, url):
            return None
    opener = urllib.request.build_opener(NoRedirect)
    try:
        response = opener.open(request, timeout=20)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        payload = response.read(2 * 1024 * 1024 + 1)
        require(len(payload) <= 2 * 1024 * 1024, 'http_response_bound')
        return response.status, json.loads(payload)


def smoke(guarded=False):
    version = BASE_VERSION if guarded else VERSION
    for _ in range(45):
        try:
            status, body = http_json(BASE_URLS[0], '/health')
            if status == 200 and body.get('database') == 'ok' and body.get('version') == version:
                break
        except Exception:
            pass
        time.sleep(1)
    else:
        raise GuardError('api_health_not_ready')
    checks = []
    for index, base in enumerate(BASE_URLS):
        status, body = http_json(base, '/health')
        require(status == 200 and body['database'] == 'ok' and body['version'] == version, 'public_or_local_health')
        status, api = http_json(base, '/openapi.json')
        require(status == 200 and api['info']['version'] == version, 'public_or_local_openapi')
        if guarded:
            for path in ('/onboarding/photo', '/style-profile/analyze'):
                status, _ = http_json(base, path, method='POST')
                require(status == 503, 'legacy_photo_not_blocked')
        else:
            require('/catalog/discovery' in api['paths'] and '/catalog/discovery/{product_id}' in api['paths'], 'discovery_routes_missing')
            for path in ('/catalog/discovery', '/catalog/discovery/00000000-0000-0000-0000-000000000000'):
                status, _ = http_json(base, path)
                require(status == 401, 'unauthenticated_discovery_not_401')
        checks.append({'surface': 'local' if index == 0 else 'public', 'health': 200, 'openapi': 200,
                       'photo_blocked': guarded, 'unauthenticated_discovery': None if guarded else 401})
    return checks


def backup():
    directory = PRIVATE / 'pre-activation-backup'
    directory.mkdir(mode=0o700)
    dump = directory / 'source.dump'
    with dump.open('xb') as output, (directory / 'dump.log').open('xb') as log:
        subprocess.run(['docker', 'exec', '-e', 'PGOPTIONS=-c default_transaction_read_only=on -c lock_timeout=5000',
                        'modish_postgres', 'pg_dump', '-U', 'modish_user', '-d', 'modish', '-Fc', '--no-owner', '--no-acl'],
                       stdout=output, stderr=log, check=True, timeout=1200)
    require(dump.stat().st_size > 1000, 'production_backup_empty')
    with dump.open('rb') as stream:
        toc = subprocess.run(['docker', 'exec', '-i', 'modish_postgres', 'pg_restore', '--list'], stdin=stream,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=120).stdout
    require(all(value in toc for value in (b'TABLE public users ', b'TABLE public products ', b'TABLE public alembic_version ')),
            'production_backup_toc_incomplete')
    (directory / 'source.toc').write_bytes(toc)
    return {'sha256': sha(dump), 'bytes': dump.stat().st_size, 'consistency': 'pg_dump_transaction_snapshot',
            'location': 'activation-private/pre-activation-backup', 'restore_automatic': False}


def worker_environment(url):
    path = PRIVATE / 'production-worker.env'
    values = {'DATABASE_URL': url, 'JWT_SECRET': secrets.token_urlsafe(48), 'APP_ENV': 'production',
              'PYTHON_DOTENV_DISABLED': '1', 'PYTHONPATH': '/app', 'AI_API_KEY': '', 'OPENAI_API_KEY': '',
              'AI_BASE_URL': 'http://127.0.0.1:9', 'ADMITAD_ENABLED': 'false'}
    with path.open('x') as stream:
        for key, value in values.items():
            require('\n' not in value and '\r' not in value, 'worker_env_line_injection')
            stream.write(key + '=' + value + '\n')
    return path


def worker(envfile, command, *, name, entrypoint, mounts=(), env=(), timeout=7200):
    container_name = 'modish_build14_activation_' + uuid4().hex
    args = ['docker', 'run', '--rm', '--pull=never', '--name', container_name, '--label', 'modish.activation.owner=' + container_name,
            '--cpus=1', '--memory=1536m', '--network', NETWORK, '--env-file', str(envfile)]
    for item in env:
        args += ['-e', item]
    for path, target in mounts:
        args += ['-v', str(path) + ':' + target + ':ro']
    args += ['--entrypoint', entrypoint, IMAGE, *command]
    try:
        with (PRIVATE / (name + '.json')).open('xb') as output, (PRIVATE / (name + '.log')).open('xb') as log:
            subprocess.run(args, stdout=output, stderr=log, check=True, timeout=timeout)
    finally:
        remaining = subprocess.run(['docker', 'inspect', container_name], capture_output=True)
        if remaining.returncode == 0:
            obj = json.loads(remaining.stdout)[0]
            require(obj['Name'] == '/' + container_name and obj['Image'] == IMAGE and
                    obj['Config']['Labels'].get('modish.activation.owner') == container_name and
                    set(obj['NetworkSettings']['Networks']) == {NETWORK}, 'owned_worker_identity_changed')
            capture('docker', 'stop', '--time', '30', container_name)
    return PRIVATE / (name + '.json')


def outside_fingerprint():
    fixed = "('baon','shoppinglive','serginnetti','postmeridiem')"
    where = {'products': 'p.source IS NULL OR p.source NOT IN ' + fixed,
             'product_sources': 'p.code NOT IN ' + fixed}
    for table in ('catalog_records', 'catalog_sync_runs', 'source_rules'):
        where[table] = 'NOT EXISTS (SELECT 1 FROM product_sources s WHERE s.id=p.source_id AND s.code IN ' + fixed + ')'
    return {table: query("SELECT count(*) || ':' || md5(coalesce(string_agg(md5(row_to_json(p)::text), '' ORDER BY p.id), '')) "
                         'FROM ' + table + ' p WHERE ' + clause) for table, clause in where.items()}


def rollback(plan, *, automatic=False):
    switch_api(plan, 'rollback', ('automatic-' if automatic else 'manual-') + 'rollback-compose.log')
    checks = smoke(guarded=True)
    infrastructure_unchanged(plan)
    return {'status': 'guarded_api_rollback_complete', 'checks': checks, 'schema_and_catalogue_preserved': True,
            'photo_reservations_preserved': True, 'database_restored': False}


def activate(args):
    require(args.production_import_approved, 'explicit_production_import_approval_required')
    value = load_plan(args.plan_sha256)
    url = production_scope()
    require(snapshot() == value['production_before'] and sha(COMPOSE) == value['original_compose_sha256'] and
            query('SELECT version_num FROM alembic_version') == OLD_REVISION, 'production_changed_since_plan')
    report = json.loads(regular(R / 'bounded-report.json', value['bounded_report_sha256']).read_text())
    evidence(report)
    manifest, feeds = snapshots(report)
    require(feeds == value['feeds'], 'approved_snapshots_changed')
    regular(R / 'source.tar', ARCHIVE, limit=256 * 1024 * 1024)
    regular(R / 'release14_import_production.py', PRODUCTION_IMPORT)
    require(not (R / 'activation-started.json').exists(), 'activation_already_attempted_no_automatic_retry')
    write_json(R / 'activation-started.json', {'plan_sha256': args.plan_sha256, 'started_at': datetime.now(timezone.utc).isoformat(),
                                             'production_import_explicitly_approved': True})
    record = {'status': 'activating', 'phase': 'preconditions', 'plan_sha256': args.plan_sha256,
              'candidate_image': IMAGE, 'rollback_image': value['rollback_image'], 'production_import_approved': True,
              'import_outcome': 'not_started', 'database_restore_automatic': False}
    guarded = False
    try:
        progress(record, 'fresh_consistent_production_backup')
        record['backup'] = backup()
        envfile = worker_environment(url)
        progress(record, 'guarded_base_api_and_old_worker_drain')
        guarded = True  # Even a partial Compose switch must use the safe target.
        switch_api(value, 'rollback', 'pre-migration-guard-compose.log')
        record['guard_smoke'] = smoke(guarded=True)
        progress(record, 'additive_production_migration')
        worker(envfile, ['upgrade', REVISION], name='production-migration', entrypoint='alembic', timeout=600)
        require(query('SELECT version_num FROM alembic_version') == REVISION, 'production_migration_revision')
        record['revision'] = REVISION
        outside = outside_fingerprint()
        snapshots(report)  # Recheck age after backup, drain and migration.
        regular(R / 'release14_import_production.py', PRODUCTION_IMPORT)
        progress(record, 'atomic_approved_four_source_import')
        record['import_outcome'] = 'attempted_outcome_unconfirmed'
        imported = json.loads(worker(envfile, ['/import.py'], name='production-import', entrypoint='python',
            mounts=((R / 'release14_import_production.py', '/import.py'), (R / 'feeds', '/feeds')),
            env=('MODISH_PRODUCTION_IMPORT_APPROVED=' + APPROVAL, 'REFRESH_RUN_ID=' + manifest['run_id'],
                 'AUDIT_IMAGE_ID=' + IMAGE, 'AUDIT_ARCHIVE_SHA256=' + ARCHIVE)).read_text())
        require(imported['result'] == 'import_committed' and imported['scope'] == 'build14_four_approved_snapshots_atomic_production_import' and
                imported['production_database_connected'] is True and imported['single_transaction'] and
                imported['existing_product_ids_preserved'] and not imported['raw_timestamps_injected'] and
                not imported['other_sources_modified'] and set(imported['committed_sources']) == set(FEEDS), 'production_import_result')
        record['import_outcome'] = 'committed'
        record['production_import'] = imported
        require(outside_fingerprint() == outside, 'outside_sources_changed')
        record['outside_sources_unchanged'] = True
        progress(record, 'candidate_api_only_switch')
        switch_api(value, 'candidate', 'candidate-compose.log')
        progress(record, 'local_and_public_acceptance')
        record['checks'] = smoke()
        infrastructure_unchanged(value)
        require(query('SELECT version_num FROM alembic_version') == REVISION, 'post_activation_revision_changed')
        record.update(status='deployed', image_id=inspect('modish_backend')['Image'], finished_at=datetime.now(timezone.utc).isoformat(),
                      production_database_and_redis_unchanged=True, catalogue_full_outfit_readiness='unproven')
        progress(record, 'complete')
        write_json(R / 'deployment-record.json', record)
        print(json.dumps({'status': 'deployed', 'api_version': VERSION, 'imported_sources': 4, 'schema': REVISION}), flush=True)
        return 0
    except Exception as error:
        record.update(status='failed', error_type=type(error).__name__, failed_phase=record['phase'])
        if isinstance(error, GuardError):
            record['guard'] = str(error)
        # A failed import is never retried, even if rollback was reported. A
        # lost client connection can conceal a successful PostgreSQL COMMIT.
        if guarded:
            try:
                record['api_rollback'] = rollback(value, automatic=True)
            except Exception as rollback_error:
                record['api_rollback'] = {'status': 'requires_operator_attention', 'error_type': type(rollback_error).__name__}
        record['finished_at'] = datetime.now(timezone.utc).isoformat()
        progress(record, 'failed_preserved_for_review')
        write_json(R / 'activation-failure-report.json', record)
        return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('plan', 'activate', 'rollback'))
    parser.add_argument('--bounded-report-sha256')
    parser.add_argument('--rollback-guard-sha256')
    parser.add_argument('--rollback-tests-sha256')
    parser.add_argument('--plan-sha256')
    parser.add_argument('--production-import-approved', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    require(__debug__, 'python_optimization_forbidden')
    for name in ('bounded_report_sha256', 'rollback_guard_sha256', 'rollback_tests_sha256', 'plan_sha256'):
        value = getattr(args, name)
        require(value is None or re.fullmatch(r'[0-9a-f]{64}', value), 'invalid_hash_argument')
    if args.mode == 'plan':
        require(all((args.bounded_report_sha256, args.rollback_guard_sha256, args.rollback_tests_sha256)), 'planning_hashes_required')
        plan(args)
        return 0
    require(args.plan_sha256, 'exact_plan_hash_required')
    if args.mode == 'activate':
        return activate(args)
    value = load_plan(args.plan_sha256)
    result = rollback(value)
    write_json(R / ('rollback-record-' + uuid4().hex + '.json'), result)
    print(json.dumps({'status': result['status'], 'database_restored': False}), flush=True)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({'status': 'failed', 'error_type': type(error).__name__,
                          'guard': str(error) if isinstance(error, GuardError) else None}), flush=True)
        raise SystemExit(1) from None
