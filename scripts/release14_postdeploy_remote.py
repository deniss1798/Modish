"""Read-only final verification; public metadata only, backup stays on VPS."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import urllib.error
import urllib.request

R = Path('/opt/modish-releases/20261014-build14')


def require(condition, code):
    if not condition:
        raise RuntimeError(code)


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def capture(*args):
    return subprocess.run(args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout


def inspect(name):
    return json.loads(capture('docker', 'inspect', name))[0]


def main():
    os.umask(0o077)
    require(R.resolve() == R and not R.is_symlink(), 'fixed_release_directory')
    plan = json.loads((R / 'activation-plan.json').read_text())
    deployed = json.loads((R / 'deployment-record.json').read_text())
    require(deployed['status'] == 'deployed' and deployed['import_outcome'] == 'committed', 'completed_deployment_required')
    require(inspect('modish_backend')['Image'] == plan['candidate_image'] and
            inspect('modish_backend')['State']['Running'], 'candidate_api_not_running')
    require(sha(Path('/opt/Modish/docker-compose.yml')) == plan['compose']['candidate']['sha256'] and
            sha(Path('/opt/Modish/backend/.env')) == plan['production_environment_sha256'], 'live_config_drift')
    for name in ('modish_postgres', 'modish_redis'):
        actual, expected = inspect(name), plan['production_before'][name]
        require(actual['Id'] == expected['id'] and actual['Image'] == expected['image'] and
                actual['State']['Running'] and actual['RestartCount'] == expected['restart_count'] and
                actual['State']['StartedAt'] == expected['started_at'], 'infrastructure_changed')
    directory = Path(plan['compose']['candidate']['path']).parent
    require(directory == R / 'activation-private-v2', 'backup_directory_scope')
    dump = directory / 'pre-activation-backup/source.dump'
    require(dump.is_file() and not dump.is_symlink() and dump.stat().st_size == deployed['backup']['bytes'] and
            sha(dump) == deployed['backup']['sha256'], 'backup_bytes_or_hash_changed')
    backup_location = str(dump.parent.relative_to(R))
    query = """SELECT json_build_object('revision',(SELECT version_num FROM alembic_version),
        'sources',(SELECT json_agg(row_to_json(s)) FROM (
          SELECT source,count(*) AS total_rows,
            count(*) FILTER(WHERE is_active=1 AND is_available=1 AND
              last_seen_in_feed_at>=now()-interval '72 hours' AND last_seen_in_feed_at<=now()+interval '5 minutes') AS fresh_available_rows,
            max(last_seen_in_feed_at) AS newest_stock_observation
          FROM products WHERE source IN ('baon','shoppinglive','serginnetti','postmeridiem')
          GROUP BY source ORDER BY source) s))"""
    data = json.loads(capture('docker', 'exec', '-e',
        'PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=15000 -c lock_timeout=2000',
        'modish_postgres', 'psql', '-X', '-qAt', '-U', 'modish_user', '-d', 'modish',
        '--set', 'ON_ERROR_STOP=1', '-c', query))
    require(data['revision'] == '20261013_0025' and len(data['sources']) == 4 and
            all(row['fresh_available_rows'] > 0 for row in data['sources']), 'fresh_imported_sources_missing')
    checks = []
    for base in ('http://127.0.0.1:8000', 'https://modish.org.ru'):
        with urllib.request.urlopen(base + '/health', timeout=20) as response:
            health = json.load(response)
            require(response.status == 200 and health['database'] == 'ok' and
                    health['version'] == '1.0.0-build14', 'candidate_health_version')
        with urllib.request.urlopen(base + '/openapi.json', timeout=20) as response:
            api = json.load(response)
            require(api['info']['version'] == '1.0.0-build14' and '/catalog/discovery' in api['paths'], 'candidate_routes')
        try:
            with urllib.request.urlopen(base + '/catalog/discovery', timeout=20):
                raise RuntimeError('discovery_allowed_without_authentication')
        except urllib.error.HTTPError as error:
            require(error.code == 401, 'discovery_unauthenticated_status')
        checks.append({'surface': 'public' if base.startswith('https:') else 'local',
                       'health': 200, 'openapi': 200, 'unauthenticated_catalogue': 401})
    proxy = json.loads((R / 'proxy-timeout-record.json').read_text())
    require(proxy['status'] == 'proxy_timeout_updated' and proxy['read_timeout_seconds'] == 240, 'proxy_deadline_mismatch')
    capture('nginx', '-t')
    value = {'status': 'passed', 'finished_at': datetime.now(timezone.utc).isoformat(),
             'api_version': '1.0.0-build14', 'image_id': plan['candidate_image'], 'checks': checks,
             'schema': data['revision'], 'sources': data['sources'], 'source_counts_are_fit_claims': False,
             'production_database_and_redis_preserved': True, 'compose_and_environment_match_plan': True,
             'backup': {'verified_sha256': deployed['backup']['sha256'], 'actual_location': backup_location,
                        'bytes': deployed['backup']['bytes'], 'original_report_location_erratum': deployed['backup']['location']},
             'proxy_read_timeout_seconds': 240, 'database_writes': False, 'external_ai_calls': 0}
    with (R / 'postdeploy-verification.json').open('x') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
    print(json.dumps(value), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'status': 'failed', 'error_type': type(error).__name__,
                          'guard': str(error) if isinstance(error, RuntimeError) else None}), flush=True)
        raise SystemExit(1) from None
