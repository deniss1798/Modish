"""Prepare build14 in a NEW isolated clone; never activate or import production.

Upload source.tar and the pinned helpers to the fixed release directory.
Run only on the authorized VPS after independent review of this file. Outputs,
database dump and test credentials stay on that VPS with private permissions.
Failures preserve the clone and diagnostic artifacts for inspection. This file
does not fetch feeds, install dependencies, publish ports or restart production.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import subprocess
import tarfile
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4


R = Path('/opt/modish-releases/20261014-build14')
BASE = 'sha256:4ae234fc8a1f8082bb368f4f617d187a6138bb5848c02af5e9a7a9f15a3d6d7e'
PG = 'modish_foundation_test_pg'
NETWORK = 'modish-foundation-test'
DATABASE = 'modish_build14_test'
TEST_ENV = Path('/opt/modish-releases/20260922-foundation/test-api.env')
SOURCE_FEEDS = Path('/opt/modish-releases/20261009-intent-v20/refresh-20261013/feeds')
OLD_REVISION, REVISION = '20260917_0021', '20261013_0025'
TAG = 'modish-backend:build14-20261014'
PRODUCTION_NAMES = ('modish_backend', 'modish_postgres', 'modish_redis')
FEEDS = {'baon': '19982', 'shoppinglive': '25461', 'serginnetti': '26417', 'postmeridiem': '25200'}
ORIGINAL_IMPORT_SHA = '6181045401d7eb3dbe702c68ec80d90837b9a4436136bd2914bc67b592ddad8f'
RETARGETS = (
    ('timedelta(hours=4)', 'timedelta(hours=72)'),
    ('url.database == url.username == "modish_test"', 'url.database == "modish_build14_test" and url.username == "modish_test"'),
    ('engine.url.database == engine.url.username == "modish_test"', 'engine.url.database == "modish_build14_test" and engine.url.username == "modish_test"'),
    ('("modish_test", "modish_test", 5432)', '("modish_build14_test", "modish_test", 5432)'),
    ('"20261008_0024", "test_revision_required"', '"20261013_0025", "test_revision_required"'),
    ('"fresh_four_sources_atomic_isolated_clone_import"', '"build14_four_fixed_snapshots_atomic_isolated_clone_only"'),
    ('current_ids.get(key) == pid', 'key in current_ids and current_ids[key] == pid'),
)


class GuardError(RuntimeError):
    """Messages are fixed guard codes, never external diagnostics or secrets."""


def require(condition, code):
    if not condition:
        raise GuardError(code)


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def capture(*command, **kwargs):
    return subprocess.run(command, check=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, **kwargs).stdout


def inspect(name):
    return json.loads(capture('docker', 'inspect', name))[0]


def write_json(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')


def production_snapshot():
    snapshot = {}
    for name in PRODUCTION_NAMES:
        obj = inspect(name)
        snapshot[name] = {'id': obj['Id'], 'image': obj['Image'],
                          'running': obj['State']['Running'], 'started_at': obj['State']['StartedAt'],
                          'restart_count': obj['RestartCount']}
    require(snapshot['modish_backend']['image'] == BASE, 'production_base_changed')
    require(all(item['running'] for item in snapshot.values()), 'production_not_running')
    return snapshot


def query(sql, *, database=DATABASE, production=False):
    container, user = PG, 'modish_test'
    if production:
        container = 'modish_postgres'
        values = dict(item.split('=', 1) for item in inspect(container)['Config']['Env'] if '=' in item)
        user = values['POSTGRES_USER']
        database = values.get('POSTGRES_DB', user)
    # Every psql observation, including production revision checks, is read only.
    return capture('docker', 'exec', '-e', 'PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=30000',
                   container, 'psql', '-X', '-qAt', '-U', user, '-d', database,
                   '--set', 'ON_ERROR_STOP=1', '-c', sql, text=True).strip()


def guard_scope():
    require(R.is_dir() and not R.is_symlink() and R.resolve() == R, 'fixed_release_directory_required')
    pg = inspect(PG)
    net = json.loads(capture('docker', 'network', 'inspect', NETWORK))[0]
    require(pg['State']['Running'] and net['Internal'], 'internal_running_test_postgres_required')
    require(set(pg['NetworkSettings']['Networks']) == {NETWORK}, 'test_postgres_network_mismatch')
    require(not pg.get('HostConfig', {}).get('PortBindings'), 'test_postgres_port_published')
    require(all(inspect(name)['Id'] != pg['Id'] and NETWORK not in inspect(name)['NetworkSettings']['Networks']
                for name in PRODUCTION_NAMES), 'test_network_overlaps_production')
    require(query('SELECT current_database() || \':\' || current_user', database='modish_test') ==
            'modish_test:modish_test', 'test_postgres_identity_mismatch')
    require(query("SELECT count(*) FROM pg_database WHERE datname = 'modish_build14_test'", database='modish_test') == '0',
            'new_clone_already_exists')
    require(query('SELECT version_num FROM alembic_version', production=True) == OLD_REVISION,
            'unexpected_production_revision')


def verify_helpers(args):
    helpers = {}
    expected_helpers = [('check_photo_postgres.py', args.photo_helper_sha256),
                        ('release14_import_clone.py', args.import_helper_sha256),
                        ('intent_v20_refresh_import.py', ORIGINAL_IMPORT_SHA)]
    if args.discovery_helper_sha256:
        expected_helpers.append(('check_discovery_postgres.py', args.discovery_helper_sha256))
    for name, expected in expected_helpers:
        path = R / name
        require(path.is_file() and not path.is_symlink(), 'regular_helper_required')
        require(path.stat().st_size < 1024 * 1024 and digest(path) == expected, 'helper_hash_mismatch')
        helpers[name] = expected
    old = (R / 'intent_v20_refresh_import.py').read_text(encoding='utf-8')
    for before, after in RETARGETS:
        require(old.count(before) == 1, 'import_retarget_source_changed')
        old = old.replace(before, after)
    require(old.encode() == (R / 'release14_import_clone.py').read_bytes(), 'import_retarget_not_exact')
    return helpers


def extract_archive(expected):
    path = R / 'source.tar'
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 256 * 1024 * 1024,
            'bounded_regular_archive_required')
    require(digest(path) == expected, 'archive_hash_mismatch')
    require(not (R / 'backend').exists(), 'immutable_extraction_already_exists')
    with tarfile.open(path) as archive:
        members = archive.getmembers()
        names = [item.name for item in members]
        require(len(names) == len(set(names)) and len(names) <= 10000, 'duplicate_or_excess_archive_members')
        require(all(item.isfile() and 0 <= item.size <= 32 * 1024 * 1024 for item in members),
                'regular_bounded_archive_members_required')
        require(sum(item.size for item in members) <= 256 * 1024 * 1024, 'archive_expansion_limit')
        manifest = json.load(archive.extractfile('source-manifest.json'))
        require(manifest['base_image'] == BASE and manifest['migration'] == REVISION, 'manifest_base_or_revision')
        require(set(names) == set(manifest['files']) | {'source-manifest.json'}, 'archive_manifest_members_mismatch')
        require('backend/alembic/versions/20261013_0025_photo_analysis_reservations.py' in manifest['files'],
                'photo_migration_missing')
        # Verify the complete archive before writing any extracted source.
        for name, expected_hash in manifest['files'].items():
            posix = PurePosixPath(name)
            require(name.startswith('backend/') and posix.as_posix() == name and
                    '\\' not in name and '..' not in posix.parts and not posix.is_absolute(), 'unsafe_archive_path')
            require(not {'.git', '__pycache__'} & set(posix.parts) and
                    not (posix.name.startswith('.env') and posix.name != '.env.example') and
                    not posix.name.endswith(('.pem', '.key', '.sqlite', '.db')),
                    'private_or_generated_archive_member')
            require(hashlib.sha256(archive.extractfile(name).read()).hexdigest() == expected_hash, 'member_hash_mismatch')
        for member in members:
            if member.name == 'source-manifest.json':
                continue
            target = R / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as stream:
                shutil.copyfileobj(archive.extractfile(member), stream)
            target.chmod(0o700 if member.mode & 0o111 else 0o600)
    return manifest


def clone_environment():
    require(TEST_ENV.is_file() and not TEST_ENV.is_symlink(), 'test_environment_file_required')
    values = dict(line.split('=', 1) for line in TEST_ENV.read_text().splitlines()
                  if '=' in line and not line.startswith('#'))
    original = values['DATABASE_URL']
    parsed = urlsplit(original.replace('postgresql+psycopg://', 'postgresql://'))
    require(parsed.scheme == 'postgresql' and parsed.hostname == PG and parsed.username == 'modish_test' and
            parsed.path == '/modish_test' and parsed.port in (None, 5432) and not parsed.query and not parsed.fragment,
            'test_database_url_mismatch')
    new_url = urlunsplit((original.split('://', 1)[0], parsed.netloc, '/' + DATABASE, '', ''))
    values = {'DATABASE_URL': new_url, 'JWT_SECRET': secrets.token_urlsafe(48), 'APP_ENV': 'test',
              'PYTHON_DOTENV_DISABLED': '1', 'PYTHONPATH': '/app', 'ADMITAD_ENABLED': 'false',
              'AI_API_KEY': '', 'OPENAI_API_KEY': '', 'AI_BASE_URL': 'http://127.0.0.1:9',
              'RATE_LIMIT_PER_MINUTE': '100000'}
    destination = R / 'private/test-api.env'
    with destination.open('x') as stream:
        for name, value in values.items():
            require('\n' not in value and '\r' not in value, 'environment_line_injection')
            stream.write(name + '=' + value + '\n')
    return destination


def logged(command, name, *, timeout=1200, input=None):
    with (R / 'private' / name).open('xb') as log:
        subprocess.run(command, input=input, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=timeout)


def image_build(manifest):
    require(inspect(BASE)['Id'] == BASE, 'pinned_base_unavailable')
    dependencies = """from importlib.metadata import version
from pip._vendor.packaging.requirements import Requirement
from pathlib import Path
assert not Path('/app/.env').exists()
for line in Path('/check-requirements.txt').read_text().splitlines():
 if line.strip() and not line.startswith('#'):
  r=Requirement(line)
  if r.marker is None or r.marker.evaluate(): assert r.specifier.contains(version(r.name), prereleases=True)
"""
    logged(['docker', 'run', '--rm', '--pull=never', '--network=none',
            '-v', str(R / 'backend/requirements.txt') + ':/check-requirements.txt:ro',
            '--entrypoint', 'python', BASE, '-c', dependencies], 'dependency-check.log')
    tag = 'modish-backend:build14-pinned-dependencies-20261014'
    capture('docker', 'tag', BASE, tag)
    require(inspect(tag)['Id'] == BASE, 'base_tag_changed')
    dockerfile = R / 'Offline.Dockerfile'
    with dockerfile.open('x') as stream:
        stream.write('FROM ' + tag + '\nWORKDIR /app\nCOPY . .\nENV PYTHONUNBUFFERED=1\nCMD ["scripts/entrypoint.sh"]\n')
    logged(['docker', 'build', '--pull=false', '--network=none', '-f', str(dockerfile), '-t', TAG,
            str(R / 'backend')], 'build.log')
    require(inspect(tag)['Id'] == BASE, 'base_tag_changed_during_build')
    image = inspect(TAG)['Id']
    require(re.fullmatch(r'sha256:[0-9a-f]{64}', image), 'immutable_image_required')
    code = """import json,sys,hashlib
from pathlib import Path
m=json.load(sys.stdin)
assert not Path('/app/.env').exists()
for name,expected in m['files'].items():
 assert hashlib.sha256(Path('/app/'+name.removeprefix('backend/')).read_bytes()).hexdigest()==expected
"""
    logged(['docker', 'run', '--rm', '--pull=never', '-i', '--network=none', '--entrypoint', 'python', image,
            '-c', code], 'image-files.log', input=json.dumps(manifest).encode())
    return image


def run_units(image, expected_tests):
    command = ['docker', 'run', '--rm', '--pull=never', '--network=none', '-e', 'DATABASE_URL=sqlite://',
               '-e', 'JWT_SECRET=' + secrets.token_urlsafe(48), '-e', 'APP_ENV=test', '-e', 'PYTHON_DOTENV_DISABLED=1',
               '--entrypoint', 'python', image, '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_*.py', '-v']
    logged(command, 'unit-tests.log', timeout=1800)
    lines = (R / 'private/unit-tests.log').read_text(errors='replace').splitlines()
    summaries = [m for line in lines if (m := re.fullmatch(r'Ran (\d+) tests? in ([0-9.]+)s', line.strip()))]
    require(len(summaries) == 1 and int(summaries[0][1]) == expected_tests and
            any(re.fullmatch(r'OK(?: \([^\n]*\))?', line.strip()) for line in lines), 'unit_summary_mismatch')
    return {'status': 'passed', 'count': expected_tests, 'seconds': float(summaries[0][2]), 'network': 'none'}


def restore_new_clone():
    values = dict(item.split('=', 1) for item in inspect('modish_postgres')['Config']['Env'] if '=' in item)
    user = values['POSTGRES_USER']
    database = values.get('POSTGRES_DB', user)
    backup = R / 'privatebackup/source.dump'
    with backup.open('xb') as output, (R / 'private/pg-dump.log').open('xb') as log:
        subprocess.run(['docker', 'exec', '-e', 'PGOPTIONS=-c default_transaction_read_only=on -c lock_timeout=5000',
                        'modish_postgres', 'pg_dump', '-U', user, '-d', database, '-Fc', '--no-owner', '--no-acl'],
                       stdout=output, stderr=log, check=True, timeout=1200)
    require(backup.stat().st_size > 1000, 'backup_empty')
    with backup.open('rb') as stream:
        require(stream.read(5) == b'PGDMP', 'backup_format_mismatch')
    with backup.open('rb') as stream:
        toc = subprocess.run(['docker', 'exec', '-i', PG, 'pg_restore', '--list'], stdin=stream,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=120).stdout
    require(all(token in toc for token in (b'TABLE public users ', b'TABLE public products ', b'TABLE public alembic_version ')),
            'backup_toc_incomplete')
    (R / 'privatebackup/source.toc').write_bytes(toc)
    require(query('SELECT version_num FROM alembic_version', production=True) == OLD_REVISION, 'production_revision_changed')
    # Recheck immediately before CREATE DATABASE. createdb itself fails on races.
    require(query("SELECT count(*) FROM pg_database WHERE datname = 'modish_build14_test'", database='modish_test') == '0',
            'new_clone_already_exists')
    capture('docker', 'exec', PG, 'createdb', '-U', 'modish_test', '--maintenance-db=modish_test', DATABASE)
    with backup.open('rb') as stream, (R / 'private/pg-restore.log').open('xb') as log:
        subprocess.run(['docker', 'exec', '-i', PG, 'pg_restore', '--exit-on-error', '--no-owner', '--no-acl',
                        '-U', 'modish_test', '-d', DATABASE], stdin=stream, stdout=log, stderr=subprocess.STDOUT,
                       check=True, timeout=1200)
    require(query('SELECT version_num FROM alembic_version') == OLD_REVISION, 'restored_revision_mismatch')
    return {'status': 'restored_new_database', 'bytes': backup.stat().st_size, 'sha256': digest(backup),
            'dump_consistency': 'pg_dump_transaction_snapshot', 'source_revision': OLD_REVISION}


def clone_container(image, envfile, command, *, name, entrypoint='python', mounts=(), env=(), timeout=1800):
    """Only exact, privately labelled ephemeral containers may be stopped here."""
    container_name = 'modish_build14_prepare_' + uuid4().hex
    label = 'modish.prepare.run=' + container_name
    args = ['docker', 'run', '--rm', '--pull=never', '--name', container_name, '--label', label,
            '--cpus=1', '--memory=1536m', '--network', NETWORK, '--env-file', str(envfile)]
    for item in env:
        args += ['-e', item]
    for source, target in mounts:
        args += ['-v', str(source) + ':' + target + ':ro']
    args += ['--entrypoint', entrypoint, image, *command]
    try:
        with (R / 'private' / (name + '.json')).open('xb') as output, (R / 'private' / (name + '.log')).open('xb') as log:
            subprocess.run(args, stdout=output, stderr=log, check=True, timeout=timeout)
    finally:
        remains = subprocess.run(['docker', 'inspect', container_name], capture_output=True)
        if remains.returncode == 0:
            obj = json.loads(remains.stdout)[0]
            require(obj['Name'] == '/' + container_name and obj['Image'] == image and
                    obj['Config']['Labels'].get('modish.prepare.run') == container_name and
                    set(obj['NetworkSettings']['Networks']) == {NETWORK}, 'owned_test_container_identity_changed')
            values = dict(item.split('=', 1) for item in obj['Config']['Env'] if '=' in item)
            url = urlsplit(values['DATABASE_URL'].replace('postgresql+psycopg://', 'postgresql://'))
            require(url.hostname == PG and url.path == '/' + DATABASE and url.username == 'modish_test',
                    'owned_test_container_database_changed')
            # Stop only our exact container; never remove images, databases or
            # debug files. Auto-remove is Docker's original lifecycle setting.
            capture('docker', 'stop', '--time', '30', container_name)
    return R / 'private' / (name + '.json')


def copy_snapshots():
    require(SOURCE_FEEDS.is_dir() and SOURCE_FEEDS.resolve() == SOURCE_FEEDS and not SOURCE_FEEDS.is_symlink(),
            'fixed_feed_directory_required')
    source = SOURCE_FEEDS / 'download-manifest.json'
    require(source.is_file() and not source.is_symlink() and source.stat().st_size < 1024 * 1024, 'feed_manifest_bound')
    manifest = json.loads(source.read_text())
    manifest_sha = digest(source)
    require(manifest['result'] == 'download_complete' and manifest['scope'] == 'four_fixed_admitad_https_exports_no_database' and
            manifest['observation_clock_basis'] == 'real_download_request_start_utc', 'feed_observation_provenance')
    require(len(manifest['sources']) == 4 and {row['code'] for row in manifest['sources']} == set(FEEDS), 'feed_source_set')
    require(isinstance(manifest['run_id'], str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', manifest['run_id']), 'feed_run_id')
    destination = R / 'feeds'
    destination.mkdir()
    now, reports = datetime.now(timezone.utc), []
    for row in manifest['sources']:
        code = row['code']
        require(row['feed_id'] == FEEDS[code] and row['filename'] == code + '.csv', 'feed_identity_mismatch')
        observed, finished = (datetime.fromisoformat(row[key]) for key in ('observed_at', 'download_finished_at'))
        require(all(stamp.tzinfo is not None and stamp.utcoffset() == timedelta(0) for stamp in (observed, finished)) and
                now - timedelta(hours=72) <= observed <= finished <= now + timedelta(minutes=5), 'feed_observation_expired')
        path = SOURCE_FEEDS / row['filename']
        require(path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= 256 * 1024 * 1024 and
                path.stat().st_size == row['bytes'] and digest(path) == row['sha256'], 'feed_snapshot_hash_mismatch')
        shutil.copyfile(path, destination / row['filename'])
        require(digest(destination / row['filename']) == row['sha256'] and digest(path) == row['sha256'], 'feed_copy_changed')
        reports.append({key: row[key] for key in ('code', 'feed_id', 'bytes', 'sha256', 'observed_at', 'download_finished_at')})
    shutil.copyfile(source, destination / source.name)
    require(digest(source) == digest(destination / source.name) == manifest_sha, 'feed_manifest_changed')
    return manifest['run_id'], reports


def outside_sources_fingerprint():
    fixed = "('baon','shoppinglive','serginnetti','postmeridiem')"
    clauses = {
        'products': 'p.source IS NULL OR p.source NOT IN ' + fixed,
        'product_sources': 'p.code NOT IN ' + fixed,
        'catalog_records': 'NOT EXISTS (SELECT 1 FROM product_sources s WHERE s.id=p.source_id AND s.code IN ' + fixed + ')',
        'catalog_sync_runs': 'NOT EXISTS (SELECT 1 FROM product_sources s WHERE s.id=p.source_id AND s.code IN ' + fixed + ')',
        'source_rules': 'NOT EXISTS (SELECT 1 FROM product_sources s WHERE s.id=p.source_id AND s.code IN ' + fixed + ')',
    }
    return {table: query("SELECT count(*) || ':' || md5(coalesce(string_agg(md5(row_to_json(p)::text), '' ORDER BY p.id), '')) "
                         'FROM ' + table + ' p WHERE ' + clause) for table, clause in clauses.items()}


def progress(report, phase):
    report['phase'] = phase
    print(json.dumps({'progress': phase, 'production_database_writes': False}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive-sha256', required=True)
    parser.add_argument('--expected-tests', type=int, required=True)
    parser.add_argument('--photo-helper-sha256', required=True)
    parser.add_argument('--import-helper-sha256', required=True)
    parser.add_argument('--discovery-helper-sha256')
    args = parser.parse_args()
    require(__debug__, 'python_optimization_forbidden_for_pinned_helpers')
    require(args.expected_tests > 0 and all(re.fullmatch(r'[0-9a-f]{64}', value) for value in
            (args.archive_sha256, args.photo_helper_sha256, args.import_helper_sha256)), 'explicit_hashes_and_test_count_required')
    require(args.discovery_helper_sha256 is None or re.fullmatch(r'[0-9a-f]{64}', args.discovery_helper_sha256),
            'discovery_helper_hash_invalid')
    os.umask(0o077)
    report = {'status': 'preparing', 'phase': 'scope_guards', 'ready_for_activation': False,
              'production_database_writes': False, 'production_catalogue_imported': False,
              'archive_sha256': args.archive_sha256, 'started_at': datetime.now(timezone.utc).isoformat()}
    before = None
    try:
        guard_scope()
        require(not any((R / name).exists() for name in ('private', 'privatebackup', 'prepared-report.json', 'failure-report.json')),
                'release_preparation_already_started')
        (R / 'private').mkdir(mode=0o700)
        (R / 'privatebackup').mkdir(mode=0o700)
        before = production_snapshot()
        report['production_before'] = before
        report['helpers'] = verify_helpers(args)
        manifest = extract_archive(args.archive_sha256)
        envfile = clone_environment()
        progress(report, 'offline_build_and_units')
        image = image_build(manifest)
        report['image_id'] = image
        report['unit_tests'] = run_units(image, args.expected_tests)
        progress(report, 'readonly_backup_and_new_clone')
        report['backup'] = restore_new_clone()
        report['test_pg_container_id'] = inspect(PG)['Id']
        progress(report, 'clone_migration')
        clone_container(image, envfile, ['upgrade', REVISION], name='migration', entrypoint='alembic')
        require(query('SELECT version_num FROM alembic_version') == REVISION, 'clone_migration_revision')
        report['clone_revision'] = REVISION
        user_count = query('SELECT count(*) FROM users')
        progress(report, 'postgres_photo_and_registration_concurrency')
        require(digest(R / 'check_photo_postgres.py') == args.photo_helper_sha256, 'photo_helper_changed')
        photo = json.loads(clone_container(image, envfile,
            ['/audit.py', '--isolated-write-test', '--expected-host', PG, '--expected-database', DATABASE],
            name='photo-postgres', mounts=((R / 'check_photo_postgres.py', '/audit.py'),)).read_text())
        require(photo['status'] == 'passed' and photo['fixture_cleanup'] == 'committed' and photo['revision'] == REVISION and
                photo['external_provider_calls'] == 0 and len(photo['checks']) == 5 and
                all(check['status'] == 'passed' for check in photo['checks'].values()), 'postgres_photo_acceptance_failed')
        require(query('SELECT count(*) FROM users') == user_count, 'fixture_user_cleanup_mismatch')
        report['photo_postgres'] = photo
        progress(report, 'copy_existing_fixed_snapshots')
        refresh_id, report['feed_snapshots'] = copy_snapshots()
        outside = outside_sources_fingerprint()
        progress(report, 'isolated_clone_catalogue_import')
        require(digest(R / 'release14_import_clone.py') == args.import_helper_sha256, 'import_helper_changed')
        imported = json.loads(clone_container(image, envfile, ['/audit.py'], name='clone-import', timeout=7200,
            mounts=((R / 'release14_import_clone.py', '/audit.py'), (R / 'feeds', '/feeds')),
            env=('REFRESH_RUN_ID=' + refresh_id, 'AUDIT_IMAGE_ID=' + image,
                 'AUDIT_ARCHIVE_SHA256=' + args.archive_sha256)).read_text())
        require(imported['result'] == 'import_committed' and
                imported['scope'] == 'build14_four_fixed_snapshots_atomic_isolated_clone_only' and
                imported['single_transaction'] is True and imported['existing_product_ids_preserved'] is True and
                imported['raw_timestamps_injected'] is False and imported['other_sources_modified'] is False and
                set(imported['committed_sources']) == set(FEEDS), 'clone_import_acceptance_failed')
        require(outside_sources_fingerprint() == outside, 'outside_sources_changed')
        require(query('SELECT count(*) FROM users') == user_count, 'restored_user_count_changed')
        discovery = None
        if args.discovery_helper_sha256:
            progress(report, 'ten_profile_catalogue_and_discovery_services')
            require(digest(R / 'check_discovery_postgres.py') == args.discovery_helper_sha256, 'discovery_helper_changed')
            discovery = json.loads(clone_container(image, envfile,
                ['/audit.py', '--isolated-write-test', '--expected-host', PG, '--expected-database', DATABASE],
                name='discovery-postgres', mounts=((R / 'check_discovery_postgres.py', '/audit.py'),)).read_text())
            require(discovery['status'] == 'passed' and discovery['profile_count'] == 10 and
                    discovery['fixture_cleanup'] == 'rolled_back' and discovery['users_restored'] is True and
                    discovery['external_provider_calls'] == 0,
                    'discovery_acceptance_failed')
            require(query('SELECT count(*) FROM users') == user_count and outside_sources_fingerprint() == outside,
                    'discovery_fixture_or_catalogue_state_changed')
        require(verify_helpers(args) == report['helpers'] and digest(R / 'source.tar') == args.archive_sha256,
                'immutable_inputs_changed')
        require(production_snapshot() == before, 'production_metadata_changed')
        require(query('SELECT version_num FROM alembic_version', production=True) == OLD_REVISION, 'production_revision_changed')
        report.update(status='code_and_clone_import_checked_catalogue_acceptance_pending', phase='catalogue_acceptance_pending',
                      clone_import=imported, outside_sources_unchanged=True, production_metadata_unchanged=True,
                      pending_checks=['ten_profile_catalogue_and_discovery_services', 'discovery_http_smoke', 'production_import_explicit_approval',
                                      'reviewed_activation_and_application_rollback'],
                      catalogue_full_outfit_readiness='unproven', finished_at=datetime.now(timezone.utc).isoformat())
        if discovery is not None:
            report.update(status='prepared_ready_for_review', phase='isolated_checks_complete',
                          discovery_postgres=discovery,
                          pending_checks=['discovery_http_smoke', 'production_import_explicit_approval',
                                          'reviewed_activation_and_application_rollback'])
        write_json(R / 'prepared-report.json', report)
        print(json.dumps({'status': report['status'], 'phase': report['phase'], 'ready_for_activation': False,
                          'unit_test_count': args.expected_tests, 'photo_checks': 5, 'imported_sources': 4}))
        return 0
    except Exception as exc:
        report.update(status='failed', error_type=type(exc).__name__, finished_at=datetime.now(timezone.utc).isoformat())
        if isinstance(exc, GuardError):
            report['guard'] = str(exc)
        if before is not None:
            try:
                report['production_metadata_unchanged'] = production_snapshot() == before
            except Exception:
                report['production_metadata_unchanged'] = 'verification_failed'
        # Do not overwrite earlier evidence or remove a failed clone.
        if R.is_dir() and R.resolve() == R and not (R / 'failure-report.json').exists():
            write_json(R / 'failure-report.json', report)
        print(json.dumps({key: report[key] for key in ('status', 'phase', 'error_type', 'guard') if key in report}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
