"""Host-side NEW clone rehearsal only. No SSH, downloads or production writes.

Review before upload/execution. Existing test databases and env files are never
reused or dropped. Failure leaves the private stage/clone/logs for inspection.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import selectors
import shutil
import signal
import subprocess
from time import monotonic
from uuid import UUID

ARCHIVE = '5184f378245db0073154369f1b6e083c8d584cb9006788993cd4727866e26c24'
DOCKER = ['docker', '--host', 'unix:///var/run/docker.sock']
COMMAND_ENV = {'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
               'HOME': '/root', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8', 'TZ': 'UTC'}
PROD = {
    'modish_backend': ('7658a4e5e4cacc252630658fbe71d8bb10298a5769cb8958271c6f6482b9e950',
                       'sha256:eeea30209dcaa366fd936b6181ce84d47fc475a342df257893d58bb725dac1ec'),
    'modish_postgres': ('987af124c33aa2984c6d4379417036f4278f3024ba75c6f145dcd29838da667e',
                        'sha256:5d143123fdf80462d1778cd4f24b9f7ca13c87174bca19141fb194c5a1ebca59'),
    'modish_redis': ('7d097417acdcb98861c1b4f1df83fcf562db12c231d6f2f44f25180f7308e615',
                     'sha256:3e1b24a1a8f24ff926b15e5ace8c38a03e5657fb66e1fc7e5188e315aa5fa094'),
}
TEST_PG = 'modish_foundation_test_pg'
TEST_ID = '881ea4c21dc23bbbe79889d3779b2500707af32e4705a619790e680688d6786f'
NETWORK = 'modish-foundation-test'
ADMIN = 'modish_test'
PG_ENV = {'PGHOST', 'PGHOSTADDR', 'PGPORT', 'PGDATABASE', 'PGUSER', 'PGPASSWORD',
          'PGSERVICE', 'PGSERVICEFILE', 'PGPASSFILE', 'PGOPTIONS', 'PGSSLMODE'}
MIB = 1024 * 1024
R: Path | None = None
STAGE_VALIDATED = False
STEP = 'arguments'
LAST_COMMAND = None
MIGRATION_CONTAINER: tuple[str, str] | None = None
REPORT = {'scope': 'new_uuid_build15_clone_only', 'result': 'running',
          'production_database_writes_by_helper': False, 'downloads': False}


class GuardError(RuntimeError):
    pass


def require(condition, code):
    if not condition:
        raise GuardError(code)


def json_file(path, data):
    with path.open('x', encoding='utf-8') as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(data, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def terminate(process):
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def run(label, argv, *, data=None, input_file=None, output_file=None,
        timeout=40, max_stdout=4 * MIB, max_stderr=4 * MIB):
    """Bound both streams while collecting stderr privately, never printing it."""
    global LAST_COMMAND
    require(re.fullmatch(r'[a-z][a-z0-9_]{0,60}', label) is not None, 'fixed_command_label_required')
    LAST_COMMAND = label
    require(not (data is not None and input_file is not None), 'one_input_required')
    require(data is None or len(data) <= 65536, 'stdin_sql_bound')
    error_path = R / ('clone-' + label + '.private-errors.log')
    with error_path.open('xb') as errors, io.BytesIO() as memory_output:
        os.fchmod(errors.fileno(), 0o600)
        output = output_file if output_file is not None else memory_output
        process = subprocess.Popen(argv, stdin=(subprocess.PIPE if data is not None else input_file or subprocess.DEVNULL),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, env=COMMAND_ENV)
        selector = selectors.DefaultSelector()
        totals = {'stdout': 0, 'stderr': 0}
        try:
            pending_input = memoryview(data) if data is not None else None
            if data is not None:
                os.set_blocking(process.stdin.fileno(), False)
                selector.register(process.stdin, selectors.EVENT_WRITE, ('stdin', None, None))
            selector.register(process.stdout, selectors.EVENT_READ, ('stdout', output, max_stdout))
            selector.register(process.stderr, selectors.EVENT_READ, ('stderr', errors, max_stderr))
            deadline = monotonic() + timeout
            while selector.get_map():
                require(monotonic() < deadline, 'command_deadline_exceeded')
                for key, _ in selector.select(min(0.5, max(0, deadline - monotonic()))):
                    if key.data[0] == 'stdin':
                        if pending_input:
                            try:
                                written = os.write(key.fileobj.fileno(), pending_input[:4096])
                                pending_input = pending_input[written:]
                            except BrokenPipeError:
                                pending_input = None
                        if not pending_input:
                            selector.unregister(key.fileobj)
                            key.fileobj.close()
                        continue
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                        continue
                    kind, destination, bound = key.data
                    totals[kind] += len(chunk)
                    require(totals[kind] <= bound, 'command_output_bound_exceeded')
                    destination.write(chunk)
            require(process.wait(timeout=3) == 0, 'command_failed')
            if output_file is not None:
                output_file.flush()
                os.fsync(output_file.fileno())
                return totals
            memory_output.seek(0)
            return memory_output.read()
        except Exception:
            terminate(process)
            raise
        finally:
            selector.close()
            if process.stdout and not process.stdout.closed:
                process.stdout.close()
            if process.stderr and not process.stderr.closed:
                process.stderr.close()
            if process.stdin and not process.stdin.closed:
                process.stdin.close()


def inspect(name, label):
    return json.loads(run(label, [*DOCKER, 'inspect', name]))[0]


def production_snapshot(label):
    result = {}
    for index, (name, (identity, image)) in enumerate(PROD.items()):
        obj = inspect(name, label + '_' + str(index))
        require(obj['Id'] == identity and obj['Image'] == image and obj['State']['Running'],
                'production_identity_or_running_state_changed')
        env_names = {value.split('=', 1)[0] for value in obj['Config'].get('Env', [])}
        if name == 'modish_postgres':
            require(not PG_ENV.intersection(env_names), 'production_pg_connection_override_present')
        result[name] = {'id': obj['Id'], 'image': obj['Image'],
                        'started_at': obj['State']['StartedAt'], 'restart_count': obj['RestartCount']}
    return result


def guard_test(label):
    obj = inspect(TEST_PG, label)
    require(obj['Id'] == TEST_ID and obj['Image'] == PROD['modish_postgres'][1]
            and obj['State']['Running'], 'test_pg_identity_changed')
    require(set(obj['NetworkSettings']['Networks']) == {NETWORK}
            and not obj['HostConfig'].get('PortBindings'), 'test_pg_isolation_required')
    environment = dict(value.split('=', 1) for value in obj['Config'].get('Env', []) if '=' in value)
    require(environment.get('POSTGRES_USER') == ADMIN and not PG_ENV.intersection(environment),
            'test_pg_admin_or_connection_environment_changed')
    return obj


def sql(label, statement, *, database=ADMIN, production=False, write=False):
    require(not production or not write, 'production_write_forbidden')
    container, user = ('modish_postgres', 'modish_user') if production else (TEST_PG, ADMIN)
    if not production:
        guard_test(label + '_guard')
    options = '-c statement_timeout=30000 -c lock_timeout=2000'
    if not write:
        options += ' -c default_transaction_read_only=on'
    argv = [*DOCKER, 'exec', '-i', '-e', 'PGHOST=/var/run/postgresql', '-e', 'PGOPTIONS=' + options,
            container, 'timeout', '-k', '5s', '40s', 'psql', '-X', '-qAt', '-U', user,
            '-d', 'modish' if production else database, '--set', 'ON_ERROR_STOP=1']
    return run(label, argv, data=statement.encode(), timeout=50).decode().strip()


def counts(label, database):
    names = sql(label + '_tables', "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname='public' ORDER BY tablename;", database=database).splitlines()
    require(0 < len(names) <= 100 and all(re.fullmatch(r'[a-z][a-z0-9_]{0,62}', name) for name in names),
            'bounded_application_table_names_required')
    fields = ','.join("'" + name + "',(SELECT count(*) FROM public.\"" + name + '\")' for name in names)
    return json.loads(sql(label, 'SELECT json_build_object(' + fields + ');', database=database))


def integrity(label, database, tables):
    """Hash full clone rows, normalizing only deliberate migration changes.

    Primary-key order preserves deterministic identity/linkage comparisons.
    SQL serializes each complete row internally; only 32-hex digests leave PG.
    Neither source user data nor raw/canonical product JSON is returned.
    """
    require(re.fullmatch(r'modish_refresh15_[0-9a-f]{32}', database) is not None,
            'uuid_clone_integrity_database_required')
    require(isinstance(tables, dict) and 0 < len(tables) <= 100
            and all(isinstance(table, str) and re.fullmatch(r'[a-z][a-z0-9_]{0,62}', table)
                    for table in tables), 'bounded_clone_table_identifiers_required')
    primary_keys = json.loads(sql(label + '_primary_keys', r'''
SELECT coalesce(json_object_agg(tablename, columns), '{}'::json) FROM (
  SELECT t.relname AS tablename, json_agg(a.attname ORDER BY k.position) AS columns
  FROM pg_index i JOIN pg_class t ON t.oid=i.indrelid
  JOIN pg_namespace n ON n.oid=t.relnamespace
  CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, position)
  JOIN pg_attribute a ON a.attrelid=t.oid AND a.attnum=k.attnum
  WHERE i.indisprimary AND n.nspname='public' AND k.position <= i.indnkeyatts
  GROUP BY t.relname
) AS primary_columns;
''', database=database))
    require(isinstance(primary_keys, dict) and set(primary_keys) == set(tables),
            'all_clone_tables_require_primary_keys')
    output = {}
    for index, table in enumerate(sorted(tables)):
        columns = primary_keys[table]
        require(isinstance(columns, list) and 0 < len(columns) <= 16
                and all(isinstance(column, str) and re.fullmatch(r'[a-z][a-z0-9_]{0,62}', column)
                        for column in columns), 'bounded_primary_key_identifiers_required')
        payload = 'to_jsonb(t)'
        if table == 'products':
            payload += " || jsonb_build_object('price',t.price::numeric(14,2),'old_price',t.old_price::numeric(14,2))"
        elif table == 'outfits':
            payload += " || jsonb_build_object('total_price',t.total_price::numeric(14,2))"
        elif table == 'users':
            payload += " - 'recommendation_revision'"
        elif table == 'alembic_version':
            payload += " - 'version_num'"
        order = ','.join('t."' + column + '"' for column in columns)
        statement = "SELECT md5(coalesce(string_agg(md5((" + payload + ")::text), '' ORDER BY " + order + "), '')) FROM public.\"" + table + '\" t;'
        digest = sql(label + '_' + str(index), statement, database=database)
        require(re.fullmatch(r'[0-9a-f]{32}', digest) is not None, 'row_integrity_digest_required')
        output[table] = digest
    return output


def require_image(image, identity, run_id):
    require(image['Id'] == identity and image['Config'].get('WorkingDir') == '/app',
            'expected_build15_image_required')
    labels = image['Config'].get('Labels') or {}
    require(labels.get('org.modish.refresh_run') == run_id
            and labels.get('org.modish.source_sha256') == ARCHIVE,
            'image_run_and_archive_labels_required')


def migration_stop(name, identity, image):
    obj = inspect(name, 'migration_timeout_identity')
    require(obj['Id'] == identity and obj['Image'] == image
            and obj['Config'].get('Labels', {}).get('modish.refresh15.run_id') == REPORT['run_id']
            and set(obj['NetworkSettings']['Networks']) == {NETWORK}, 'owned_migration_container_required')
    if obj['State']['Running']:
        run('migration_timeout_stop', [*DOCKER, 'stop', '--time', '10', name], timeout=20)


def main():
    global R, STEP, MIGRATION_CONTAINER, STAGE_VALIDATED
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--image-id', required=True)
    parser.add_argument('--archive-sha256', required=True)
    parser.add_argument('--root', required=True)
    args = parser.parse_args()
    require(os.name == 'posix' and os.geteuid() == 0, 'linux_root_host_required')
    require(re.fullmatch(r'[0-9a-f]{32}', args.run_id) is not None
            and UUID(args.run_id).version == 4, 'uuid4_run_required')
    require(args.archive_sha256 == ARCHIVE and re.fullmatch(r'sha256:[0-9a-f]{64}', args.image_id),
            'reviewed_archive_and_immutable_image_required')
    expected = Path('/opt/modish-releases/20261021-catalog-refresh15-' + args.run_id)
    R = Path(args.root)
    require(R == expected and R.is_dir() and not R.is_symlink() and R.resolve() == R,
            'fixed_private_staging_root_required')
    require(R.stat().st_uid == 0 and R.stat().st_mode & 0o777 == 0o700, 'private_root_mode_required')
    require(not any((R / name).exists() for name in
            ('clone-started.json', 'clone-report.json', 'clone-failed.json', 'clone.env', 'production.dump',
             'clone-docker-client', 'clone-integrity-before.json', 'clone-integrity-after.json')),
            'new_clone_stage_outputs_required')
    STAGE_VALIDATED = True
    os.umask(0o077)
    REPORT.update(run_id=args.run_id, image_id=args.image_id, archive_sha256=ARCHIVE,
                  started_at=datetime.now(timezone.utc).isoformat())
    json_file(R / 'clone-started.json', REPORT)
    client_config = R / 'clone-docker-client'
    client_config.mkdir(mode=0o700)
    json_file(client_config / 'config.json', {})
    DOCKER.extend(['--config', str(client_config)])
    STEP = 'preflight'
    baseline = production_snapshot('production_before')
    test = guard_test('test_preflight')
    require(all(NETWORK not in inspect(name, 'production_network_' + str(index))['NetworkSettings']['Networks']
                for index, name in enumerate(PROD)), 'production_not_on_test_network_required')
    network = json.loads(run('network_preflight', [*DOCKER, 'network', 'inspect', NETWORK]))[0]
    require(network['Internal'], 'internal_test_network_required')
    image = json.loads(run('image_preflight', [*DOCKER, 'image', 'inspect', args.image_id]))[0]
    require_image(image, args.image_id, args.run_id)
    # This is availability of a timeout executable, not execution of a DB command.
    run('prod_timeout_available', [*DOCKER, 'exec', 'modish_postgres', 'timeout', '--help'])
    run('test_timeout_available', [*DOCKER, 'exec', TEST_PG, 'timeout', '--help'])
    require(sql('production_revision', 'SELECT version_num FROM alembic_version;', production=True) == '20261013_0025',
            'source_schema_0025_required')
    require(sql('test_admin', "SELECT current_user || ':' || rolsuper::text FROM pg_roles WHERE rolname=current_user;") == ADMIN + ':true',
            'isolated_test_admin_required')
    size = int(sql('source_size', "SELECT pg_database_size(current_database());", production=True))
    free = shutil.disk_usage(R).free
    require(0 < size <= 8 * 1024**3 and free >= size * 2 + 2 * 1024**3, 'clone_disk_space_bound')
    database, role = 'modish_refresh15_' + args.run_id, 'refresh15_' + args.run_id
    require(sql('new_database_absent', "SELECT count(*) FROM pg_database WHERE datname='" + database + "';") == '0',
            'new_clone_database_already_exists')
    require(sql('new_role_absent', "SELECT count(*) FROM pg_roles WHERE rolname='" + role + "';") == '0',
            'new_clone_role_already_exists')
    REPORT.update(database=database, role=role, source_database_bytes=size, free_bytes_before=free)
    STEP = 'fresh_readonly_dump'
    dump = R / 'production.dump'
    with dump.open('xb') as output:
        os.fchmod(output.fileno(), 0o600)
        run('production_dump', [*DOCKER, 'exec', '-e', 'PGHOST=/var/run/postgresql', '-e',
            'PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=300000 -c lock_timeout=5000',
            'modish_postgres', 'timeout', '-k', '10s', '900s', 'pg_dump', '-Fc', '--no-owner',
            '--no-privileges', '--lock-wait-timeout=5s', '-U', 'modish_user', '-d', 'modish'],
            output_file=output, timeout=925, max_stdout=8 * 1024**3)
    digest = hashlib.sha256()
    with dump.open('rb') as stream:
        require(stream.read(5) == b'PGDMP', 'custom_dump_header_required')
        stream.seek(0)
        for chunk in iter(lambda: stream.read(MIB), b''):
            digest.update(chunk)
    require(dump.stat().st_size > 0, 'nonempty_completed_dump_required')
    REPORT.update(dump_bytes=dump.stat().st_size, dump_sha256=digest.hexdigest(), dump_mode='0600')
    STEP = 'new_test_role_and_database'
    password = secrets.token_urlsafe(48)
    STEP = 'new_private_environment'
    values = {'DATABASE_URL': 'postgresql+psycopg://' + role + ':' + password + '@' + TEST_PG + ':5432/' + database,
              'JWT_SECRET': secrets.token_urlsafe(48), 'APP_ENV': 'test', 'PYTHON_DOTENV_DISABLED': '1',
              'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONOPTIMIZE': '0', 'ADMITAD_ENABLED': 'false',
              'STYLE_VISUAL_ENABLED': 'false', 'CLOSED_ALPHA_ENABLED': 'false', 'ALLOW_DEV_ANALYZE': 'false'}
    env = R / 'clone.env'
    with env.open('x', encoding='utf-8') as output:
        os.fchmod(output.fileno(), 0o600)
        output.write(''.join(key + '=' + value + '\n' for key, value in values.items()))
        output.flush()
        os.fsync(output.fileno())
    values.clear()
    STEP = 'new_test_role_and_database'
    # Only fixed validated identifiers and a freshly generated URL-safe secret.
    sql('create_new_role', 'CREATE ROLE "' + role + '" LOGIN PASSWORD \'' + password +
        "' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 20;", write=True)
    password = None
    sql('create_new_database', 'CREATE DATABASE "' + database + '" OWNER "' + role + '" TEMPLATE template0;', write=True)
    sql('restrict_new_database', 'REVOKE ALL ON DATABASE "' + database + '" FROM PUBLIC;', write=True)
    STEP = 'restore_new_clone'
    guard_test('restore_guard')
    with dump.open('rb') as source:
        run('restore_clone', [*DOCKER, 'exec', '-i', '-e', 'PGHOST=/var/run/postgresql', '-e',
            'PGOPTIONS=-c statement_timeout=120000 -c lock_timeout=5000', TEST_PG,
            'timeout', '-k', '10s', '1200s', 'pg_restore', '--exit-on-error', '--no-owner', '--no-privileges',
            '--no-comments', '--no-publications', '--no-subscriptions', '--no-tablespaces',
            '--role', role, '-U', ADMIN, '-d', database], input_file=source, timeout=1225)
    require(sql('restored_revision', 'SELECT version_num FROM alembic_version;', database=database) == '20261013_0025',
            'restored_schema_0025_required')
    before = counts('clone_counts_before', database)
    before_integrity = integrity('clone_integrity_before', database, before)
    json_file(R / 'clone-integrity-before.json', {'counts': before, 'row_integrity': before_integrity})
    REPORT['counts_before_migration'] = before
    REPORT['row_integrity_before_migration'] = before_integrity
    STEP = 'migrate_new_clone_0028'
    name = 'modish_refresh15_migration_' + args.run_id
    identity = run('create_migration_container', [*DOCKER, 'create', '--pull=never', '--name', name,
        '--cpus=1', '--memory=1g', '--pids-limit=128', '--cap-drop=ALL', '--security-opt=no-new-privileges',
        '--read-only', '--network', NETWORK, '--env-file', str(env),
        '-e', 'PGOPTIONS=-c statement_timeout=120000 -c lock_timeout=5000',
        '--label', 'modish.refresh15.run_id=' + args.run_id, '--entrypoint', 'python',
        args.image_id, '-m', 'alembic', 'upgrade', '20261015_0028']).decode().strip()
    require(re.fullmatch(r'[0-9a-f]{64}', identity), 'migration_container_id_required')
    MIGRATION_CONTAINER = (name, identity)
    try:
        run('migration', [*DOCKER, 'start', '-a', name], timeout=600)
    except Exception:
        migration_stop(name, identity, args.image_id)
        raise
    migration = inspect(name, 'migration_completion')
    require(migration['Id'] == identity and migration['Image'] == args.image_id
            and not migration['State']['Running'] and migration['State']['ExitCode'] == 0,
            'migration_container_success_required')
    STEP = 'verify_new_clone'
    require(sql('migrated_revision', 'SELECT version_num FROM alembic_version;', database=database) == '20261015_0028',
            'target_schema_0028_required')
    after = counts('clone_counts_after', database)
    after_integrity = integrity('clone_integrity_after', database, after)
    json_file(R / 'clone-integrity-after.json', {'counts': after, 'row_integrity': after_integrity})
    require(after == before, 'table_counts_changed_during_migration')
    require(after_integrity == before_integrity, 'normalized_clone_rows_changed_during_migration')
    types = json.loads(sql('migrated_types', "SELECT json_agg(json_build_object('table',table_name,'column',column_name,'type',data_type,'precision',numeric_precision,'scale',numeric_scale,'nullable',is_nullable,'default',column_default) ORDER BY table_name,column_name) FROM information_schema.columns WHERE table_schema='public' AND ((table_name='products' AND column_name IN ('price','old_price')) OR (table_name='outfits' AND column_name='total_price') OR (table_name='fit_profiles' AND column_name='gender_target') OR (table_name='users' AND column_name='recommendation_revision'));", database=database))
    require(len(types) == 5, 'exact_migration_columns_required')
    for item in types:
        if item['column'] in ('price', 'old_price', 'total_price'):
            require(item['type'] == 'numeric' and item['precision'] == 14 and item['scale'] == 2, 'numeric_14_2_required')
        elif item['column'] == 'gender_target':
            require(item['nullable'] == 'YES' and item['default'] is None, 'nullable_gender_without_default_required')
        else:
            require(item['type'] == 'integer' and item['nullable'] == 'NO' and item['default'] in ('0', '0::integer'),
                    'recommendation_revision_integer_default_required')
    require(sql('revision_values', 'SELECT count(*) FROM users WHERE recommendation_revision <> 0 OR recommendation_revision IS NULL;', database=database) == '0',
            'initial_recommendation_revisions_zero_required')
    require(production_snapshot('production_after') == baseline, 'production_containers_changed')
    guard_test('test_after')
    REPORT.update(result='clone_migrated_verified', source_revision='20261013_0025',
                  target_revision='20261015_0028', counts_after_migration=after,
                  row_integrity_after_migration=after_integrity,
                  normalized_full_row_integrity_preserved=True, image_labels_verified=True,
                  migration_columns=types, migration_container_id=identity,
                  production_container_identity_unchanged=True, finished_at=datetime.now(timezone.utc).isoformat())
    json_file(R / 'clone-report.json', REPORT)
    print(json.dumps(REPORT))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        failure = {'scope': 'new_uuid_build15_clone_only', 'result': 'failed', 'stage': STEP,
                   'command': LAST_COMMAND,
                   'error_type': type(error).__name__, 'raw_error_omitted': True,
                   'production_database_writes_by_helper': False,
                   'artifacts_preserved': True, 'finished_at': datetime.now(timezone.utc).isoformat()}
        if isinstance(error, GuardError) and re.fullmatch(r'[a-z][a-z0-9_]{0,100}', str(error)):
            failure['check'] = str(error)
        failure['run_id'] = REPORT.get('run_id')
        if STAGE_VALIDATED and R is not None and R.is_dir() and R.resolve() == R and R.stat().st_uid == 0:
            try:
                json_file(R / 'clone-failed.json', failure)
            except Exception:
                pass
        print(json.dumps(failure))
        raise SystemExit(1) from None
