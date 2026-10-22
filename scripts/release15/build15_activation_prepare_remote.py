"""Prepare a private deployment proposal; no activation, SQL, SSH or downloads.

Only docker inspect/image inspect are allowed. All production changes, backups,
migrations, imports, compose config/up and timer operations are deliberately absent.
The owner approval token is never placed in any process environment by this file.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
from time import monotonic
from urllib.parse import unquote, urlsplit
from uuid import UUID

RUN = '9c4c8c7fe5a045068cde76d81df12104'
ARCHIVE = '5184f378245db0073154369f1b6e083c8d584cb9006788993cd4727866e26c24'
IMAGE = 'sha256:633bce227d3ce3277137c77f32f2c6e83bb338d91784803df424d7592a464aec'
MANIFEST = '5d2dde18bcb09cde948d044bc57d2178fd035aebc0698a0996d79dd5ab4ce23c'
PROOFS = {
    'clone-report.json': 'a91130f331c9a909116e15a7eb587afde5a27db2fdd04a996d60b54e549733f0',
    'import-report.json': '12d52089beaf1b3801dc0005740a525977a57317a46ec84b1251cd32dbd0c60a',
    'qa-report.json': '36e1ee48acfa1502529867f040e2a0908d7c112246f7942b97b85adee19e7830',
}
PROD = {
    'backend': {'name': 'modish_backend',
        'id': '7658a4e5e4cacc252630658fbe71d8bb10298a5769cb8958271c6f6482b9e950',
        'image': 'sha256:eeea30209dcaa366fd936b6181ce84d47fc475a342df257893d58bb725dac1ec'},
    'postgres': {'name': 'modish_postgres',
        'id': '987af124c33aa2984c6d4379417036f4278f3024ba75c6f145dcd29838da667e',
        'image': 'sha256:5d143123fdf80462d1778cd4f24b9f7ca13c87174bca19141fb194c5a1ebca59'},
    'redis': {'name': 'modish_redis',
        'id': '7d097417acdcb98861c1b4f1df83fcf562db12c231d6f2f44f25180f7308e615',
        'image': 'sha256:3e1b24a1a8f24ff926b15e5ace8c38a03e5657fb66e1fc7e5188e315aa5fa094'},
}
FEEDS = {'baon': '19982', 'shoppinglive': '25461', 'serginnetti': '26417', 'postmeridiem': '25200'}
APPROVAL = 'APPROVE_BUILD15_AND_FOUR_SOURCE_PRODUCTION_IMPORT'
COMMAND = ['python', '-m', 'uvicorn', 'app.main:app', '--host', '0.0.0.0', '--port', '8000']
POLICY = {'APP_ENV': 'production', 'CLOSED_ALPHA_ENABLED': 'false',
          'STYLE_VISUAL_ENABLED': 'false', 'ALLOW_DEV_ANALYZE': 'false'}
PG_ENV = {'PGHOST', 'PGHOSTADDR', 'PGPORT', 'PGDATABASE', 'PGUSER', 'PGPASSWORD',
          'PGSERVICE', 'PGSERVICEFILE', 'PGPASSFILE', 'PGOPTIONS', 'PGSSLMODE'}
ROOT = None
DEADLINE = None


def require(value, code):
    if not value:
        raise RuntimeError(code)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def private(path, data):
    with path.open('xb') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()


def bounded_file(path, bound):
    require(path.is_file() and not path.is_symlink() and path.stat().st_uid == 0
            and path.stat().st_mode & 0o777 == 0o600 and path.stat().st_size <= bound,
            'private_regular_input_required')
    return path.read_bytes()


def inspect_json(arguments):
    # Arguments are fixed literals/immutable IDs, never discovered commands.
    require(monotonic() < DEADLINE, 'prepare_total_deadline_exceeded')
    argv = ['docker', '--host', 'unix:///var/run/docker.sock', '--config', str(ROOT / 'docker-client'), *arguments]
    process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, start_new_session=True,
        env={'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
             'HOME': '/root', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'})
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, 'stdout')
    selector.register(process.stderr, selectors.EVENT_READ, 'stderr')
    output = {'stdout': bytearray(), 'stderr': bytearray()}
    end = min(DEADLINE, monotonic() + 10)
    try:
        while selector.get_map():
            require(monotonic() < end, 'docker_metadata_timeout')
            for key, _ in selector.select(min(0.5, max(0, end - monotonic()))):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                require(len(output[key.data]) + len(chunk) <= 1024 * 1024, 'docker_metadata_output_bound')
                output[key.data].extend(chunk)
        require(process.wait(timeout=2) == 0, 'docker_metadata_failed')
        parsed = json.loads(output['stdout'])
        require(isinstance(parsed, list) and len(parsed) == 1, 'one_metadata_object_required')
        return parsed[0]
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=2)
        selector.close()
        process.stdout.close()
        process.stderr.close()


def environment(obj):
    result = {}
    values = obj['Config'].get('Env', [])
    require(len(values) <= 512, 'environment_count_bound')
    for value in values:
        require(isinstance(value, str) and '=' in value and len(value) <= 32768
                and not any(ord(c) < 32 for c in value), 'safe_single_line_environment_required')
        key, item = value.split('=', 1)
        require(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key) and key not in result,
                'unique_environment_name_required')
        result[key] = item
    return result


def main():
    global ROOT, DEADLINE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--plan-id', required=True)
    args = parser.parse_args()
    require(os.name == 'posix' and os.geteuid() == 0, 'linux_root_required')
    require(re.fullmatch(r'[0-9a-f]{32}', args.plan_id) and UUID(args.plan_id).version == 4,
            'new_uuid4_plan_id_required')
    ROOT = Path(args.root)
    require(ROOT == Path('/opt/modish-releases/20261022-build15-activation-plan-' + args.plan_id)
            and ROOT.is_dir() and not ROOT.is_symlink() and ROOT.resolve() == ROOT
            and ROOT.stat().st_uid == 0 and ROOT.stat().st_mode & 0o777 == 0o700,
            'new_private_plan_stage_required')
    require(not any((ROOT / name).exists() for name in (
        'docker-client', 'activation-plan.json', 'candidate.compose.private.json',
        'candidate.docker-env.private', 'current-runtime.private.json', 'current-compose.private.yml',
        'prepare-report.json', *PROOFS)),
            'new_plan_artifacts_required')
    DEADLINE = monotonic() + 60
    os.umask(0o077)
    rehearsal = Path('/opt/modish-releases/20261021-catalog-refresh15-' + RUN)
    require(rehearsal.is_dir() and not rehearsal.is_symlink() and rehearsal.resolve() == rehearsal
            and rehearsal.stat().st_uid == 0 and rehearsal.stat().st_mode & 0o777 == 0o700,
            'reviewed_rehearsal_stage_required')
    proof_data, proof_bytes = {}, {}
    for name, expected in PROOFS.items():
        raw = bounded_file(rehearsal / name, 131072)
        require(digest(raw) == expected, 'completed_proof_hash_required')
        proof_bytes[name] = raw
        proof_data[name] = json.loads(raw)
        require(proof_data[name].get('run_id') == RUN
                and proof_data[name].get('archive_sha256') == ARCHIVE,
                'matching_rehearsal_proof_required')
    clone, imported, qa = (proof_data[name] for name in PROOFS)
    require(clone.get('result') == 'clone_migrated_verified'
            and clone.get('normalized_full_row_integrity_preserved') is True
            and clone.get('image_id') == IMAGE and clone.get('target_revision') == '20261015_0028',
            'clone_migration_proof_required')
    require(imported.get('result') == 'atomic_clone_import_verified'
            and imported.get('commit_state') == 'committed' and imported.get('needs_review') is False
            and imported.get('image_id') == IMAGE and imported.get('manifest_sha256') == MANIFEST
            and imported.get('existing_product_ids_preserved') is True
            and imported.get('non_target_sources_preserved') is True,
            'atomic_import_proof_required')
    require(qa.get('status') == 'passed' and qa.get('profile_count') == 10
            and qa.get('external_network_attempts') == 0 and qa.get('fixture_cleanup_completed') is True
            and qa.get('production_container_identity_unchanged') is True
            and qa.get('http', {}).get('real_swipes') == 15
            and qa['http'].get('first_feed_rows') == 10 and qa['http'].get('second_feed_rows') == 10
            and qa['http'].get('cross_page_identity_repeats') == 0,
            'ten_profiles_and_real_http_proof_required')
    feeds_directory = rehearsal / 'feeds'
    require(feeds_directory.is_dir() and not feeds_directory.is_symlink()
            and feeds_directory.resolve() == feeds_directory,
            'regular_snapshot_directory_required')
    manifest_raw = bounded_file(feeds_directory / 'download-manifest.json', 16384)
    require(digest(manifest_raw) == MANIFEST, 'reviewed_manifest_required')
    manifest = json.loads(manifest_raw)
    require(manifest.get('run_id') == RUN and manifest.get('result') == 'download_complete'
            and len(manifest['sources']) == 4
            and {value['code'] for value in manifest['sources']} == set(FEEDS),
            'complete_fixed_source_manifest_required')
    now = datetime.now(timezone.utc)
    for value in manifest['sources']:
        observed = datetime.fromisoformat(value['observed_at'])
        require(value['feed_id'] == FEEDS[value['code']] and observed.tzinfo is not None
                and observed.utcoffset() == timedelta(0)
                and now - timedelta(hours=72) <= observed <= now + timedelta(minutes=5),
                'fresh_real_observation_required')
    client = ROOT / 'docker-client'
    client.mkdir(mode=0o700)
    private(client / 'config.json', b'{}\n')
    objects = {}
    for key, expected in PROD.items():
        obj = inspect_json(['inspect', expected['name']])
        require(obj['Id'] == expected['id'] and obj['Image'] == expected['image']
                and obj['State']['Running'], 'production_baseline_changed')
        objects[key] = obj
    backend = objects['backend']
    labels = backend['Config'].get('Labels') or {}
    require(labels.get('com.docker.compose.project') == 'modish'
            and labels.get('com.docker.compose.service') == 'backend'
            and labels.get('com.docker.compose.project.config_files') == '/opt/Modish/docker-compose.yml',
            'actual_compose_identity_required')
    require('modish_default' in backend['NetworkSettings']['Networks']
            and set(objects['postgres']['NetworkSettings']['Networks']) == {'modish_default'},
            'production_network_identity_required')
    env = environment(backend)
    require(not PG_ENV.intersection(env), 'no_pg_connection_overrides_required')
    require('PRODUCTION_APPROVAL_TOKEN' not in env and 'PLAN_SHA256' not in env,
            'runtime_must_not_carry_deployment_approval')
    url = urlsplit(env.get('DATABASE_URL', ''))
    require(url.scheme in {'postgresql', 'postgresql+psycopg'} and url.hostname == 'postgres'
            and url.port in (None, 5432) and unquote(url.username or '') == 'modish_user'
            and url.path == '/modish' and not url.query and not url.fragment and bool(url.password),
            'exact_production_database_url_required')
    pg_env = environment(objects['postgres'])
    require(pg_env.get('POSTGRES_USER') == 'modish_user' and pg_env.get('POSTGRES_DB') == 'modish'
            and not PG_ENV.intersection(pg_env), 'matching_database_identity_metadata_required')
    # POSTGRES_PASSWORD initializes a new PG volume; it is not authoritative for
    # an existing cluster whose password was changed. Keep the live API URL;
    # activation must prove its actual read-only session before stopping the API.
    jwt = env.get('JWT_SECRET', '')
    require(len(jwt) >= 32 and not any(marker in jwt.lower() for marker in
            ('change_me', 'changeme', 'replace_me', 'your_', 'example', 'placeholder', '<', '>')),
            'existing_valid_jwt_required')
    require(all(not (mount['Destination'] in {'/app', '/app/.env'} or
            any(mount['Destination'] == '/app/' + part or mount['Destination'].startswith('/app/' + part + '/')
                for part in ('app', 'alembic', 'scripts', 'tests'))) for mount in backend['Mounts']),
            'runtime_must_not_override_reviewed_source_or_dotenv')
    candidate = inspect_json(['image', 'inspect', IMAGE])
    image_labels = candidate['Config'].get('Labels') or {}
    require(candidate['Id'] == IMAGE and candidate['Config'].get('Entrypoint') in ([], None)
            and candidate['Config'].get('Cmd') == COMMAND
            and image_labels.get('org.modish.refresh_run') == RUN
            and image_labels.get('org.modish.source_sha256') == ARCHIVE
            and image_labels.get('org.modish.image_attempt') == 'v3', 'verified_direct_uvicorn_image_required')
    compose = Path('/opt/Modish/docker-compose.yml')
    require(compose.is_file() and not compose.is_symlink() and compose.stat().st_uid == 0
            and compose.stat().st_size <= 262144 and compose.stat().st_mode & 0o022 == 0,
            'root_owned_compose_input_required')
    compose_raw = compose.read_bytes()
    proposed_env = {**env, **POLICY}
    require(proposed_env['JWT_SECRET'] == jwt and proposed_env['DATABASE_URL'] == env['DATABASE_URL'],
            'preserved_session_and_database_environment_required')
    env_bytes = ''.join(key + '=' + value + '\n' for key, value in sorted(proposed_env.items())).encode()
    # Compose performs interpolation even in JSON/YAML strings; $$ preserves
    # each literal dollar in existing passwords/credentials without expansion.
    proposed = {'services': {'backend': {'image': IMAGE, 'pull_policy': 'never', 'entrypoint': [],
        'command': COMMAND, 'environment': {key: value.replace('$', '$$') for key, value in proposed_env.items()}}}}
    private(ROOT / 'candidate.docker-env.private', env_bytes)
    private(ROOT / 'candidate.compose.private.json', encoded(proposed))
    private(ROOT / 'current-compose.private.yml', compose_raw)
    private(ROOT / 'current-runtime.private.json', encoded(backend))
    for name in PROOFS:
        private(ROOT / name, proof_bytes[name])
    plan = {'operation': 'build15_production_activation_plan', 'plan_id': args.plan_id, 'run_id': RUN,
        'prepared_at': now.isoformat(), 'approval_required': True, 'approval_token': APPROVAL,
        'production_changed': False, 'archive_sha256': ARCHIVE, 'image_id': IMAGE,
        'snapshot_manifest_sha256': MANIFEST, 'source_revision': '20261013_0025',
        'target_revision': '20261015_0028', 'production_containers': PROD,
        'production_database': {'host': 'postgres', 'port': 5432, 'name': 'modish', 'role': 'modish_user'},
        'feeds': FEEDS, 'proof_sha256': PROOFS, 'runtime_policy': POLICY,
        'current_compose_sha256': digest(compose_raw),
        'current_runtime_sha256': digest(encoded(backend)), 'preserve_jwt': True,
        'api_database_url_sha256': digest(env['DATABASE_URL'].encode()),
        'preserve_api_database_credentials': True,
        'candidate_environment_sha256': digest(env_bytes),
        'candidate_override_sha256': digest(encoded(proposed)),
        'pending_gates': ['explicit_owner_approval', 'reviewed_activation_driver', 'reviewed_production_importer',
            'rendered_compose_runtime_diff_review',
            'actual_api_credential_readonly_database_session',
            'maintenance_and_all_writers_fenced', 'fresh_final_backup_and_restore_proof',
            'verified_0025_to_0028_migration', 'atomic_four_source_import', 'schema_and_api_smoke',
            'compatible_rollback_or_pre_writes_final_backup_restore'],
        'rollback_old_image_on_schema0028_permitted': False,
        'regular_refresh_timer_installed': False, 'public_android_signing_ready': False}
    plan_raw = encoded(plan)
    private(ROOT / 'activation-plan.json', plan_raw)
    report = {'scope': 'private_build15_activation_proposal_only', 'status': 'prepared_for_review',
        'plan_id': args.plan_id, 'run_id': RUN, 'image_id': IMAGE, 'archive_sha256': ARCHIVE,
        'manifest_sha256': MANIFEST, 'plan_sha256': digest(plan_raw), 'production_changed': False,
        'production_database_connected': False, 'commands_run': ['docker_inspect', 'docker_image_inspect'],
        'jwt_preserved': True, 'private_environment_prepared': True, 'approval_required': True,
        'activation_executable': False, 'timer_installed': False}
    private(ROOT / 'prepare-report.json', encoded(report))
    print(json.dumps(report))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        safe = {'scope': 'private_build15_activation_proposal_only', 'status': 'failed',
                'error_type': type(error).__name__, 'production_changed': False, 'raw_error_omitted': True}
        if type(error) is RuntimeError and re.fullmatch(r'[a-z][a-z0-9_]{0,100}', str(error)):
            safe['check'] = str(error)
        print(json.dumps(safe))
        raise SystemExit(1) from None
