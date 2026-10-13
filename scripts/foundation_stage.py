"""Fresh isolated PostgreSQL clone; migration rollback and catalog replay checks."""
from pathlib import Path
from urllib.parse import quote
import json
import os
import secrets
import shutil
import subprocess
import time

R = Path('/opt/modish-releases/20260922-foundation')
B = Path('/var/backups/modish/20260922-foundation')
BASE = 'sha256:420c4ff4467bd995cfd11f9598c0f90988e79c1e4faf33d004f11f69c87c201f'
TAG = 'modish-backend:foundation-20260922'
NETWORK = 'modish-foundation-test'
PG = 'modish_foundation_test_pg'


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def inspect(name):
    return json.loads(subprocess.check_output(['docker', 'inspect', name]))[0]


def main():
    os.umask(0o077)
    assert inspect('modish_backend')['Image'] == BASE
    assert shutil.disk_usage('/opt').free > 3_000_000_000
    state = {n: inspect(n)['Id'] for n in ('modish_backend', 'modish_postgres', 'modish_redis')}
    assert subprocess.run(['docker', 'inspect', PG], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0
    assert subprocess.run(['docker', 'network', 'inspect', NETWORK], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0
    assert json.loads((R/'audit-report.json').read_text())['transaction_read_only']
    assert '\nOK\n' in (R/'unit-tests.log').read_text()
    B.mkdir(parents=True, exist_ok=True, mode=0o700)
    env = dict(v.split('=', 1) for v in inspect('modish_postgres')['Config']['Env'] if '=' in v)
    with (B/'clone-source.dump').open('wb') as dump:
        run('docker', 'exec', 'modish_postgres', 'pg_dump', '-U', env['POSTGRES_USER'],
            '-d', env['POSTGRES_DB'], '-Fc', stdout=dump)
    password = secrets.token_urlsafe(32)
    (R/'test-postgres.env').write_text(f'POSTGRES_USER=modish_test\nPOSTGRES_DB=modish_test\nPOSTGRES_PASSWORD={password}\n')
    # No production credentials, provider keys or feeds in the clone API environment.
    (R/'test-api.env').write_text('\n'.join([
        f'DATABASE_URL=postgresql+psycopg://modish_test:{quote(password,safe="")}@{PG}:5432/modish_test',
        'JWT_SECRET='+secrets.token_urlsafe(48), 'ADMIN_TOKEN='+secrets.token_urlsafe(48),
        'REDIS_URL=redis://127.0.0.1:6379/15', 'RATE_LIMIT_PER_MINUTE=100000',
        'PYTHONPATH=/app', 'PYTHON_DOTENV_DISABLED=1', 'APP_ENV=test', 'ADMITAD_ENABLED=false'])+'\n')
    run('docker', 'network', 'create', '--internal', NETWORK, stdout=subprocess.DEVNULL)
    run('docker', 'run', '-d', '--name', PG, '--network', NETWORK,
        '--env-file', str(R/'test-postgres.env'), 'postgres:16', stdout=subprocess.DEVNULL)
    for _ in range(45):
        if subprocess.run(['docker', 'exec', PG, 'pg_isready', '-U', 'modish_test'],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            break
        time.sleep(1)
    else:
        raise RuntimeError('clone_not_ready')
    with (B/'clone-source.dump').open('rb') as dump, (R/'restore.log').open('w') as log:
        run('docker', 'exec', '-i', PG, 'pg_restore', '--no-owner', '--no-acl', '--exit-on-error',
            '-U', 'modish_test', '-d', 'modish_test', stdin=dump, stdout=log, stderr=subprocess.STDOUT)
    print('isolated_clone_ready', flush=True)
    def count():
        return subprocess.check_output(['docker','exec',PG,'psql','-U','modish_test','-d','modish_test','-Atc',
            'select count(*) from products']).decode().strip()
    products_before = count()
    with (R/'migration-test.log').open('w') as log:
        for direction, revision in [('upgrade','20260921_0022'),('downgrade','20260917_0021'),('upgrade','20260921_0022')]:
            run('docker','run','--rm','--network',NETWORK,'--env-file',str(R/'test-api.env'),
                '--entrypoint','alembic',TAG,direction,revision,stdout=log,stderr=subprocess.STDOUT)
            assert count() == products_before
    print('migration_roundtrip_passed', flush=True)
    with (R/'smoke.log').open('w') as log:
        run('docker','run','--rm','--network',NETWORK,'--env-file',str(R/'test-api.env'),
            '-v',str(R/'smoke.py')+':/smoke.py:ro','--entrypoint','python',TAG,'/smoke.py',
            stdout=log,stderr=subprocess.STDOUT)
    report = [json.loads(line) for line in (R/'smoke.log').read_text().splitlines() if line.startswith('{')][-1]
    (R/'replay-report.json').write_text(json.dumps(report, indent=2))
    assert report['result'] == 'passed'
    assert all(inspect(n)['Id'] == cid for n,cid in state.items())
    (R/'stage-report.json').write_text(json.dumps({'result':'passed','production_unchanged':True,
        'products_before':int(products_before),'migration_roundtrip':True,
        'network_internal':True,'image_id':inspect(TAG)['Id']}))
    print('isolated_stage_passed', flush=True)


if __name__ == '__main__':
    main()
