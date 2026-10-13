"""Validate immutable v26 in the existing isolated PostgreSQL clone.

No SSH, deployment, migrations, importer or production-container mutations.
Upload source.tar, this script as stage.py, the corrected v19 metrics helper as
metrics-smoke.py, and intent_v23_activation_performance.py as
activation-performance.py to the fixed release directory before invoking it.
Optionally upload audit_merchant_size_fields_v20.py as size-raw-inventory.py.
Main must wait for the fresh import and its readiness audit before launch.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
from urllib.parse import urlsplit
from uuid import uuid4


R = Path('/opt/modish-releases/20261013-intent-v26')
BASELINE_R = Path('/opt/modish-releases/20261012-intent-v24')
TEST_ENV = Path('/opt/modish-releases/20260922-foundation/test-api.env')
PG = 'modish_foundation_test_pg'
NETWORK = 'modish-foundation-test'
BASE = 'sha256:4ae234fc8a1f8082bb368f4f617d187a6138bb5848c02af5e9a7a9f15a3d6d7e'
BASELINE_IMAGE = 'sha256:9b9662779677e2bb490efe8b8b82c91ea4bae0b39c445f7079fa115b62e08e6f'
BASELINE_ARCHIVE_SHA = 'b7fb93daf0e66307c32935abf7d034cc365b17cddbad83adcb8c00a9931e22fc'
FRESH_R = Path('/opt/modish-releases/20261009-intent-v20')
FRESH_IMAGE = 'sha256:cfc4376eed5398c2167527779eb0586eb567dad6d7600251abfdaab32ff69fdc'
FRESH_SHA = '099dafc5f037b8afb6912bf20ed8380c748438e5949bde3d1bb62b347f035644'
TARGET_REVISION = '20261008_0024'
TAG = 'modish-backend:intent-v26-20261013'
PRODUCTION_NAMES = ('modish_backend', 'modish_postgres', 'modish_redis')
os.umask(0o077)


def inspect(name):
    return json.loads(subprocess.check_output(['docker', 'inspect', name]))[0]


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def digest(path):
    sha = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            sha.update(chunk)
    return sha.hexdigest()


def db_query(sql):
    return subprocess.check_output([
        'docker', 'exec', PG, 'psql', '-X', '-qAt', '-U', 'modish_test', '-d', 'modish_test',
        '--set', 'ON_ERROR_STOP=1', '-c', sql,
    ], text=True).strip()


def production_snapshot():
    snapshot = {}
    for name in PRODUCTION_NAMES:
        container = inspect(name)
        snapshot[name] = {'id': container['Id'], 'image': container['Image'],
                          'running': container['State']['Running'],
                          'started_at': container['State']['StartedAt'],
                          'restart_count': container['RestartCount']}
    assert snapshot['modish_backend']['image'] == BASE
    assert all(container['running'] for container in snapshot.values())
    return snapshot


def guard_scope():
    assert R == Path('/opt/modish-releases/20261013-intent-v26') and R.is_dir()
    assert R.resolve() == R and not R.is_symlink()
    pg = inspect(PG)
    assert pg['State']['Running']
    network = json.loads(subprocess.check_output(['docker', 'network', 'inspect', NETWORK]))[0]
    assert network['Internal'], 'An internal test network is required'
    assert set(pg['NetworkSettings']['Networks']) == {NETWORK}
    assert not pg.get('HostConfig', {}).get('PortBindings')
    values = dict(line.split('=', 1) for line in TEST_ENV.read_text().splitlines()
                  if '=' in line and not line.startswith('#'))
    url = urlsplit(values['DATABASE_URL'].replace('postgresql+psycopg://', 'postgresql://'))
    assert url.scheme == 'postgresql'
    assert url.hostname == PG and url.path == '/modish_test' and url.username == 'modish_test'
    assert url.port in (None, 5432) and not url.query and not url.fragment
    assert db_query("SELECT current_database() || ':' || current_user") == 'modish_test:modish_test'
    assert db_query('SELECT version_num FROM alembic_version') == TARGET_REVISION
    assert db_query('SELECT count(*) FROM shopping_intents') == '0'


def guard_refresh_idle():
    # Read-only safety check. A fresh import/readiness audit owns this clone;
    # never compare images while one of those v20 helpers is still running.
    names = subprocess.check_output(['docker', 'ps', '--format', '{{.Names}}'], text=True).splitlines()
    assert not any(name.startswith(('modish_v20_refresh_', 'modish_v20_refresh_retry_'))
                   for name in names), 'Wait for fresh import and readiness audit to finish'


def completed_refresh_report(test_pg_id):
    path = FRESH_R / 'refresh-20261013/refresh-report.json'
    assert path.is_file() and not path.is_symlink() and path.stat().st_size <= 1024 * 1024, \
        'Completed fresh import and readiness report required before launch'
    refreshed = json.loads(path.read_text())
    assert refreshed['result'] == 'fresh_import_and_audit_complete' and refreshed['phase'] == 'complete'
    assert refreshed['image_id'] == FRESH_IMAGE and refreshed['archive_sha256'] == FRESH_SHA
    assert refreshed['test_pg_container_id'] == test_pg_id and refreshed['production_unchanged'] is True
    return {'path': str(path), 'sha256': digest(path),
            'products_after_import': refreshed['catalogue_after_import']['products']}


def catalogue_snapshot():
    # Only aggregates are retained. This hash notices changed source timestamps
    # or commerce flags between images without logging individual product IDs.
    return {'products': int(db_query('SELECT count(*) FROM products')),
            'stock_state_sha256': hashlib.sha256(db_query("""
                SELECT md5(coalesce(string_agg(
                    id || ':' || coalesce(last_seen_in_feed_at::text, '') || ':' ||
                    coalesce(is_active::text, '') || ':' || coalesce(is_available::text, '') || ':' ||
                    coalesce(is_deleted_from_feed::text, '') || ':' || coalesce(availability_status, ''),
                    '|' ORDER BY id), '')) FROM products
            """).encode()).hexdigest()}


def fresh_observation_count(as_of):
    # Parse and canonicalize before embedding a timestamp into fixed audit SQL.
    stamp = datetime.fromisoformat(as_of)
    assert stamp.tzinfo is not None and stamp.utcoffset().total_seconds() == 0
    iso = stamp.isoformat()
    return int(db_query("SELECT count(*) FROM products WHERE last_seen_in_feed_at >= "
                        "TIMESTAMPTZ '" + iso + "' - interval '72 hours' AND last_seen_in_feed_at <= "
                        "TIMESTAMPTZ '" + iso + "' + interval '5 minutes'"))


def verified_manifest(path, expected_sha, *, extract=False):
    assert digest(path) == expected_sha, 'Immutable archive hash does not match'
    with tarfile.open(path) as archive:
        names = archive.getnames()
        assert len(names) == len(set(names)), 'Duplicate archive members are forbidden'
        manifest_member = archive.getmember('source-manifest.json')
        assert manifest_member.isfile()
        manifest = json.load(archive.extractfile(manifest_member))
        assert manifest['base_image'] == BASE and manifest['migration'] == TARGET_REVISION
        assert set(names) == set(manifest['files']) | {'source-manifest.json'}
        for name, expected in manifest['files'].items():
            member = archive.getmember(name)
            assert name.startswith('backend/') and member.isfile(), 'Backend regular files only'
            assert '\\' not in name and '..' not in PurePosixPath(name).parts
            assert PurePosixPath(name).as_posix() == name
            target = (R / name).resolve()
            assert target.is_relative_to(R.resolve())
            assert name != 'backend/.env' and '/.git/' not in name
            data = archive.extractfile(member).read()
            assert hashlib.sha256(data).hexdigest() == expected, 'Archive member hash does not match'
            if extract:
                assert not target.exists(), 'Release extraction is immutable'
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                target.chmod(member.mode & 0o777)
    return manifest


def verify_image(image, manifest):
    assert inspect(image)['Id'] == image
    code = """import json,sys,hashlib
from pathlib import Path
m=json.load(sys.stdin)
assert not Path('/app/.env').exists()
for name,expected in m['files'].items():
    assert name.startswith('backend/')
    assert hashlib.sha256(Path('/app/'+name.removeprefix('backend/')).read_bytes()).hexdigest()==expected
"""
    run('docker', 'run', '--rm', '--pull=never', '-i', '--network', 'none',
        '--entrypoint', 'python', image, '-c', code, input=json.dumps(manifest).encode())


def parse_unit_summary(log_path, expected_tests=None):
    lines = log_path.read_text(errors='replace').splitlines()
    summaries = [re.fullmatch(r'Ran (\d+) tests? in ([0-9.]+)s', line.strip()) for line in lines]
    summaries = [match for match in summaries if match is not None]
    assert len(summaries) == 1 and int(summaries[0].group(1)) > 0, 'No complete unittest summary'
    assert any(re.fullmatch(r'OK(?: \([^\n]*\))?', line.strip()) for line in lines)
    actual = int(summaries[0].group(1))
    if expected_tests is not None:
        assert actual == expected_tests, 'Package and remote test counts differ'
    return {'count': actual, 'summary': summaries[0].group(0),
            'expected_count_from_package': expected_tests, 'exit_code': 0}


def pinned_helper(name, expected=None):
    path = R / name
    assert path.is_file() and not path.is_symlink()
    actual = digest(path)
    if expected is not None:
        assert actual == expected, 'Uploaded helper hash does not match'
    return {'filename': name, 'sha256': actual}


def run_helper(helper, *, image, archive_sha, output_name, log_name, fixed_now=None,
               clock_basis='current_wall_clock', args=(), timeout=2400):
    guard_scope()
    guard_refresh_idle()
    assert digest(R / helper['filename']) == helper['sha256']
    assert inspect(image)['Id'] == image
    container_name = 'modish_intent_v26_audit_' + uuid4().hex
    command = ['docker', 'run', '--rm', '--pull=never', '--name', container_name,
               '--cpus=0.75', '--memory=768m',
               '--network', NETWORK, '--env-file', str(TEST_ENV),
               '-e', 'PYTHON_DOTENV_DISABLED=1', '-e', 'APP_ENV=test',
               '-e', 'AUDIT_IMAGE_ID=' + image, '-e', 'AUDIT_ARCHIVE_SHA256=' + archive_sha]
    if fixed_now:
        command.extend(['-e', 'AUDIT_FIXED_NOW_UTC=' + fixed_now,
                        '-e', 'AUDIT_CLOCK_BASIS=' + clock_basis])
    command.extend(['-v', str(R / helper['filename']) + ':/audit.py:ro',
                    '--entrypoint', 'python', image, '/audit.py', *args])
    try:
        with (R / output_name).open('x') as output, (R / log_name).open('x') as error:
            run(*command, stdout=output, stderr=error, timeout=timeout)
    finally:
        # A Docker CLI timeout does not stop the container. Never leave a test
        # transaction running, and never remove a container merely by a prefix.
        remaining = subprocess.run(['docker', 'inspect', container_name], capture_output=True, text=True)
        if remaining.returncode == 0:
            container = json.loads(remaining.stdout)[0]
            assert container['Name'] == '/' + container_name and container['Image'] == image
            assert container['Config']['Cmd'] == ['/audit.py', *args]
            assert set(container['NetworkSettings']['Networks']) == {NETWORK}
            assert any(mount['Source'] == str(R / helper['filename']) and
                       mount['Destination'] == '/audit.py' and mount['RW'] is False
                       for mount in container['Mounts'])
            values = dict(value.split('=', 1) for value in container['Config']['Env'] if '=' in value)
            url = urlsplit(values['DATABASE_URL'].replace('postgresql+psycopg://', 'postgresql://'))
            assert url.hostname == PG and url.path == '/modish_test' and url.username == 'modish_test'
            run('docker', 'rm', '-f', container_name, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert digest(R / helper['filename']) == helper['sha256']
    return json.loads((R / output_name).read_text())


def write_report(name, value):
    with (R / name).open('x') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')


def validate_activation_measurement(measured, *, image, archive_sha, fixed_now,
                                    clock_basis, runs, product_count, profile_requested):
    """Check admission and rollback evidence before comparing exact ordered deck/feed IDs."""
    assert measured['scope'] == 'isolated_real_catalogue_controlled_profile'
    assert measured['image_id'] == image and measured['archive_sha256'] == archive_sha
    assert measured['fixed_now_utc'] == fixed_now and measured['clock_basis'] == clock_basis
    assert measured['clock_is_fixed_for_read_gates'] is True
    assert measured['historical_replay'] is (clock_basis == 'explicit_historical_replay')
    assert measured['test_writes_rolled_back'] is True
    assert measured['catalogue_is_synthetic'] is False
    assert measured['profile_and_reactions_are_controlled'] is True
    assert measured['stock_timestamps_modified'] is False
    assert measured['profile'] == {'clothing_size_system': 'LETTER', 'clothing_size': 'L',
                                   'shoe_size_system': 'EU', 'shoe_size': '38', 'height_cm': 168,
                                   'styles': ['casual', 'minimalism'], 'price_segment': 'mid'}
    assert measured['reaction_protocol'] == {'real_style_swipes': 15, 'like': 10, 'dislike': 5}
    assert len(measured['runs']) == runs
    assert [row['index'] for row in measured['runs']] == list(range(1, runs + 1))
    profiled = measured['profile_run']
    assert (profiled is not None) is profile_requested
    rows = [(row, False) for row in measured['runs']]
    if profiled is not None:
        assert profiled['index'] == runs + 1
        rows.append((profiled, True))
    for row, is_profiled in rows:
        assert row['profiled'] is is_profiled
        assert row['test_writes_rolled_back'] is True
        assert row['catalogue_product_count'] == product_count
        assert type(row['bootstrap_cards']) is int and row['bootstrap_cards'] >= 0
        assert type(row['eligible_deck_cards_checked']) is int
        assert row['eligible_deck_cards_checked'] == row['bootstrap_cards'], \
            'Every returned deck card must pass exact-size and fresh-stock eligibility'
        assert row['eligibility_basis'] == 'exact_confirmed_size_for_sold_garment_role_and_fresh_stock'
        assert re.fullmatch(r'[0-9a-f]{64}', row['bootstrap_ids_sha256'])
        assert type(row['activation_completed']) is bool
        required_phases = {'configure', 'cold_deck'}
        if row['activation_completed']:
            assert row['style_swipe_rated'] == 15 and row['learned_taste_from_real_swipes'] is True
            assert type(row['feed_cards']) is int and row['feed_cards'] >= 0
            assert type(row['eligible_feed_cards_checked']) is int
            assert row['eligible_feed_cards_checked'] == row['feed_cards'], \
                'Every returned feed card must pass exact-size and fresh-stock eligibility'
            assert re.fullmatch(r'[0-9a-f]{64}', row['feed_ids_sha256'])
            assert type(row['wow_outfits']) is int and row['wow_outfits'] >= 0
            if row['wow_outfits']:
                # The legacy helper only counted Wow outfits. Positive
                # cardinality requires the v23 helper's per-item proof.
                assert type(row.get('wow_items_returned')) is int and row['wow_items_returned'] > 0, \
                    'Positive Wow needs returned-item eligibility evidence from the helper'
                assert type(row['eligible_wow_items_checked']) is int
                assert row['eligible_wow_items_checked'] == row['wow_items_returned'], \
                    'Every returned Wow item must pass exact-size and fresh-stock eligibility'
            required_phases.update({'style_swipe_15', 'complete_wow', 'first_feed'})
        else:
            assert row['reason'] == 'not_enough_eligible_bootstrap_cards'
            assert row['bootstrap_cards'] < 15
            assert not any(key in row for key in ('feed_cards', 'feed_ids_sha256', 'wow_outfits'))
        assert set(row['phases']) == required_phases
        assert all(phase['profiled'] is is_profiled for phase in row['phases'].values())
    completed_runs = sum(row['activation_completed'] for row in measured['runs'])
    assert measured['completed_runs'] == completed_runs
    expected_status = ('completed_real_activation' if completed_runs == runs else
                       'insufficient_real_catalogue_bootstrap')
    assert measured['measurement_status'] == expected_status


def compare_activation_workloads(baseline, current):
    """Compare ordered selection hashes; pure retrieval optimization cannot change ranking."""
    assert baseline['profile'] == current['profile'], 'Controlled profiles must match'
    assert baseline['reaction_protocol'] == current['reaction_protocol'], 'Reaction protocols must match'
    assert baseline['fixed_now_utc'] == current['fixed_now_utc'], 'Benchmark clocks must match'
    assert baseline['clock_basis'] == current['clock_basis']
    assert len(baseline['runs']) == len(current['runs'])
    equality = []
    for before, after in zip(baseline['runs'], current['runs']):
        assert before['index'] == after['index']
        completed = before['activation_completed'] and after['activation_completed']
        # Incomplete activation has no Wow/feed output; it is explicitly
        # labelled instead of comparing absent keys as an equality proof.
        checks = {'bootstrap_count_equal': before['bootstrap_cards'] == after['bootstrap_cards'],
                  'activation_completion_equal': before['activation_completed'] == after['activation_completed'],
                  'wow_outfit_count_equal': before['wow_outfits'] == after['wow_outfits'] if completed else None,
                  'feed_count_equal': before['feed_cards'] == after['feed_cards'] if completed else None,
                  'deck_hash_equal': before['bootstrap_ids_sha256'] == after['bootstrap_ids_sha256'],
                  'feed_hash_equal': before['feed_ids_sha256'] == after['feed_ids_sha256'] if completed else None}
        counts_equal = (checks['bootstrap_count_equal'] and checks['activation_completion_equal'] and
                        (not completed or (checks['wow_outfit_count_equal'] and checks['feed_count_equal'])))
        selected_equal = (checks['deck_hash_equal'] and checks['feed_hash_equal']) if completed else None
        equality.append({'index': before['index'], **checks,
                         'admitted_output_counts_equal': counts_equal,
                         'ordered_selected_ids_equal': selected_equal,
                         'all_equal': counts_equal and selected_equal if completed else None,
                         'baseline_bootstrap_ids_sha256': before['bootstrap_ids_sha256'],
                         'current_bootstrap_ids_sha256': after['bootstrap_ids_sha256'],
                         'baseline_feed_ids_sha256': before['feed_ids_sha256'] if completed else None,
                         'current_feed_ids_sha256': after['feed_ids_sha256'] if completed else None})
    counts_unchanged = all(row['admitted_output_counts_equal'] for row in equality)
    completed = all(row['activation_completed'] for measured in (baseline, current) for row in measured['runs'])
    ids_unchanged = all(row['ordered_selected_ids_equal'] for row in equality) if completed else None
    rank_changed = any(not row['deck_hash_equal'] or row['feed_hash_equal'] is False for row in equality)
    no_wow = completed and all(row['wow_outfits'] == 0
                               for measured in (baseline, current) for row in measured['runs'])
    return {'workload_equality': equality,
            'admitted_output_counts_unchanged': counts_unchanged,
            'deck_and_feed_ids_unchanged': ids_unchanged,
            'rank_changed': rank_changed,
            'selection_comparison': ('admitted_output_counts_changed' if not counts_unchanged else
                                     'activation_incomplete_feed_not_measured' if not completed else
                                     'ordered_selected_ids_unchanged' if ids_unchanged else 'rank_changed'),
            'selection_comparison_basis': 'actual_ordered_selected_id_sha256_not_count_only',
            'wow_selection_comparison': ('activation_incomplete_wow_not_measured' if not completed else
                                         'both_images_returned_no_outfits' if no_wow else
                                         'count_only_positive_selection_equivalence_unverified'),
            'reaction_input_comparison': ('same_ordered_deck_ids' if all(row['deck_hash_equal'] for row in equality)
                                         else 'different_deck_ids_may_change_first_15_reactions_and_learned_taste'),
            'timing_comparability': ('activation_incomplete_only_common_phases_measured' if not completed and not rank_changed else
                                     'same_selected_deck_and_feed' if ids_unchanged and counts_unchanged else
                                     'same_protocol_not_same_workload'),
            'fresh_stock_checked_as_part_of_eligibility': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive_sha256')
    parser.add_argument('--expected-tests', type=int)
    parser.add_argument('--runs', type=int, default=3)
    parser.add_argument('--profile', action='store_true')
    parser.add_argument('--metrics-helper-sha256')
    parser.add_argument('--activation-helper-sha256')
    parser.add_argument('--inventory-helper-sha256')
    args = parser.parse_args()
    for value in (args.archive_sha256, args.metrics_helper_sha256,
                  args.activation_helper_sha256, args.inventory_helper_sha256):
        assert value is None or re.fullmatch(r'[0-9a-f]{64}', value)
    assert args.expected_tests is None or args.expected_tests > 0
    assert args.runs == 3, 'Immutable v26 comparison requires three unprofiled measured runs'
    assert not (R / 'stage-report.json').exists() and not (R / 'stage-failure.json').exists()
    state = production_snapshot()
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'scope': 'isolated_clone',
              'archive_sha256': args.archive_sha256, 'production_changed': False,
              'production_unchanged': False, 'product_ready': False, 'result': 'running'}
    try:
        guard_scope()
        guard_refresh_idle()
        assert shutil.disk_usage(R).free > 3_000_000_000
        test_pg_id = inspect(PG)['Id']
        report['test_pg_container_id'] = test_pg_id
        refresh_completed = completed_refresh_report(test_pg_id)
        report['completed_fresh_refresh'] = refresh_completed
        initial_catalogue = catalogue_snapshot()
        assert initial_catalogue['products'] == refresh_completed['products_after_import']
        assert not (R / 'backend').exists(), 'Only a new release workspace can be extracted'
        manifest = verified_manifest(R / 'source.tar', args.archive_sha256, extract=True)
        baseline_manifest = verified_manifest(BASELINE_R / 'source.tar', BASELINE_ARCHIVE_SHA)
        migrations = lambda files: {name: sha for name, sha in files.items()
                                   if name.startswith('backend/alembic/')}
        assert migrations(manifest['files']) == migrations(baseline_manifest['files']), \
            'This release must not change any migration source'
        report['migration_sources_unchanged_from_v24'] = True
        verify_image(BASELINE_IMAGE, baseline_manifest)
        helpers = {'metrics': pinned_helper('metrics-smoke.py', args.metrics_helper_sha256),
                   'activation': pinned_helper('activation-performance.py', args.activation_helper_sha256)}
        if (R / 'size-raw-inventory.py').exists():
            helpers['inventory'] = pinned_helper('size-raw-inventory.py', args.inventory_helper_sha256)
        elif args.inventory_helper_sha256:
            raise AssertionError('Pinned size inventory helper is missing')
        report['helpers'] = helpers
        # Libraries already exist in deployed build13. Dependency verification
        # and every unit test run without a network; no package install occurs.
        dependencies = """from importlib.metadata import version
from pip._vendor.packaging.requirements import Requirement
from pathlib import Path
reqs=[Requirement(s) for s in Path('/check-requirements.txt').read_text().splitlines() if s.strip() and not s.startswith('#')]
assert all(version(r.name) in r.specifier for r in reqs)
assert not Path('/app/.env').exists()
print('image_dependencies_verified')
"""
        run('docker', 'run', '--rm', '--pull=never', '--network', 'none',
            '-v', str(R / 'backend/requirements.txt') + ':/check-requirements.txt:ro',
            '--entrypoint', 'python', BASE, '-c', dependencies)
        dependencies_tag = 'modish-backend:intent-v26-dependencies-20261013'
        run('docker', 'tag', BASE, dependencies_tag)
        assert inspect(dependencies_tag)['Id'] == BASE
        dockerfile = R / 'Offline.Dockerfile'
        with dockerfile.open('x') as stream:
            stream.write('FROM ' + dependencies_tag + '\nWORKDIR /app\nCOPY . .\n'
                         'ENV PYTHONUNBUFFERED=1\nCMD ["scripts/entrypoint.sh"]\n')
        with (R / 'build-offline.log').open('x') as log:
            run('docker', 'build', '--pull=false', '--network=none', '-f', str(dockerfile),
                '-t', TAG, str(R / 'backend'), stdout=log, stderr=subprocess.STDOUT)
        assert inspect(dependencies_tag)['Id'] == BASE
        image = inspect(TAG)['Id']
        report['image_id'] = image
        print('image_built', flush=True)
        verify_image(image, manifest)
        print('image_sources_verified', flush=True)
        with (R / 'unit-tests.log').open('x') as log:
            run('docker', 'run', '--rm', '--pull=never', '--network', 'none',
                '-e', 'DATABASE_URL=sqlite:///:memory:', '-e', 'JWT_SECRET=isolated-tests-only',
                '-e', 'APP_ENV=test', '-e', 'PYTHON_DOTENV_DISABLED=1',
                '--entrypoint', 'python', image, '-m', 'unittest', 'discover', '-s', 'tests',
                '-p', 'test_*.py', '-v', stdout=log, stderr=subprocess.STDOUT)
        report['unit_tests'] = parse_unit_summary(R / 'unit-tests.log', args.expected_tests)
        print('unit_tests_passed ' + str(report['unit_tests']['count']), flush=True)
        metrics = run_helper(helpers['metrics'], image=image, archive_sha=args.archive_sha256,
                             output_name='metrics-smoke.json', log_name='metrics-smoke.log', timeout=180)
        assert metrics['scope'] == 'isolated_postgresql_synthetic_contract_not_real_coverage'
        assert metrics['checks_passed'] and metrics['test_writes_rolled_back']
        assert metrics['task_only_taste_unchanged'] and metrics['replacement_respects_rejected_skus']
        report['positive_pg_metrics_passed'] = True
        report['positive_pg_metrics_scope'] = metrics['scope']
        print('positive_pg_metrics_passed', flush=True)
        assert catalogue_snapshot() == initial_catalogue
        wall_clock = datetime.now(timezone.utc)
        clock_basis = 'current_wall_clock'
        fixed_now = wall_clock.isoformat()
        assert fresh_observation_count(fixed_now) > 0, \
            'Current catalogue stock observations expired; renew the four isolated feeds before benchmarking'
        report.update(fixed_now_utc=fixed_now, clock_basis=clock_basis,
                      historical_replay=clock_basis == 'explicit_historical_replay',
                      fresh_observations_at_current_wall_clock=fresh_observation_count(wall_clock.isoformat()),
                      fresh_observations_at_benchmark_clock=fresh_observation_count(fixed_now))
        reports = {}
        for label, checked_image, checked_sha in (
            ('baseline', BASELINE_IMAGE, BASELINE_ARCHIVE_SHA), ('current', image, args.archive_sha256),
        ):
            helper_args = ['--runs', str(args.runs)]
            if args.profile:
                helper_args.append('--profile')
            measured = run_helper(helpers['activation'], image=checked_image, archive_sha=checked_sha,
                                  fixed_now=fixed_now, args=helper_args,
                                  clock_basis=clock_basis,
                                  output_name=label + '-activation-performance.json',
                                  log_name=label + '-activation-errors.log')
            validate_activation_measurement(
                measured, image=checked_image, archive_sha=checked_sha, fixed_now=fixed_now,
                clock_basis=clock_basis, runs=args.runs, product_count=initial_catalogue['products'],
                profile_requested=args.profile)
            reports[label] = measured
            assert inspect(PG)['Id'] == test_pg_id, 'The same test PostgreSQL container must be used'
            assert catalogue_snapshot() == initial_catalogue
            print('performance_' + label + '_measured', flush=True)
        comparisons = []
        for phase, before in reports['baseline']['medians_seconds'].items():
            after = reports['current']['medians_seconds'].get(phase)
            if after is not None:
                comparisons.append({'phase': phase, 'baseline_median_seconds': before,
                                    'current_median_seconds': after,
                                    'speedup': before / after if after > 0 else None})
        workload_comparison = compare_activation_workloads(reports['baseline'], reports['current'])
        report.update(workload_comparison)
        report['all_returned_items_exact_size_and_fresh'] = True
        catalogue = {'scope': 'isolated_real_catalogue_activation_measurement',
                     'product_ready': False, 'baseline_image_id': BASELINE_IMAGE,
                     'baseline_archive_sha256': BASELINE_ARCHIVE_SHA,
                     'current_image_id': image, 'current_archive_sha256': args.archive_sha256,
                     'fixed_now_utc': fixed_now, 'comparisons': comparisons,
                     'clock_basis': clock_basis,
                     'historical_replay': clock_basis == 'explicit_historical_replay',
                     'all_returned_items_exact_size_and_fresh': True,
                     **workload_comparison,
                     'baseline': reports['baseline'], 'current': reports['current'],
                     'semantic_validation_passed': bool(workload_comparison['admitted_output_counts_unchanged'] and
                         workload_comparison['deck_and_feed_ids_unchanged'] is True),
                     'limitation': 'Actual-catalogue timing of one controlled LETTER L profile. '
                                   'Pure optimization must preserve exact ordered deck/feed hashes across images. '
                                   'Positive Wow selection hashes are not provided by the pinned helper; '
                                   'its returned items are checked for exact size and fresh stock. '
                                   'No human-feedback, catalogue-coverage or latency-target pass gate.'}
        write_report('catalog-report.json', catalogue)
        report['semantic_validation_passed'] = catalogue['semantic_validation_passed']
        assert workload_comparison['admitted_output_counts_unchanged'], \
            'v26 must preserve admitted deck/feed/Wow counts and activation completion'
        assert workload_comparison['deck_and_feed_ids_unchanged'] is True, \
            'Pure retrieval optimization must preserve exact ordered deck/feed hashes; incomplete activation cannot prove equivalence'
        report['activation_measurement_status'] = reports['current']['measurement_status']
        if 'inventory' in helpers:
            inventory = run_helper(helpers['inventory'], image=image, archive_sha=args.archive_sha256,
                                   output_name='size-raw-inventory.json',
                                   log_name='size-raw-inventory-errors.log', timeout=180)
            assert inventory['scope'] == 'isolated_postgresql_stored_active_size_fields_read_only'
            assert inventory['read_only_transaction'] and inventory['rollback_completed']
            assert inventory['stock_timestamps_modified'] is False
            report['size_raw_inventory_report'] = 'size-raw-inventory.json'
            print('catalog_size_inventory_measured', flush=True)
        assert catalogue_snapshot() == initial_catalogue
        guard_scope()
        assert inspect(PG)['Id'] == test_pg_id
        assert digest(Path(refresh_completed['path'])) == refresh_completed['sha256']
        assert digest(R / 'source.tar') == args.archive_sha256
        assert digest(BASELINE_R / 'source.tar') == BASELINE_ARCHIVE_SHA
        assert production_snapshot() == state
        report.update(test_revision=TARGET_REVISION,
                      migration_roundtrip='not_repeated_no_migration_change',
                      production_unchanged=True, result='validation_complete_catalogue_readiness_unproven')
        write_report('stage-report.json', report)
        print('stage_complete ' + report['result'], flush=True)
    except Exception as error:
        report['result'] = 'failed'
        report['error_type'] = type(error).__name__
        try:
            report['production_unchanged'] = production_snapshot() == state
            report['test_revision'] = db_query('SELECT version_num FROM alembic_version')
        except Exception as guard_error:
            report['final_guard_error_type'] = type(guard_error).__name__
        write_report('stage-failure.json', report)
        print('stage_failed ' + report['error_type'], flush=True)
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
