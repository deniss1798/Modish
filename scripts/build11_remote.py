"""Build/test the exact approved patch, then replace only the API container."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import time
import urllib.parse
import urllib.request

R = Path('/opt/modish-releases/20260922-build11')
B = Path('/var/backups/modish/20260922-build11')
OLD = 'modish-backend:release9-quality-20260920'
OLD_IMAGE = 'sha256:02a0a2531da09c51b8924081b67377c556e2b2fe771bcc5a184b88117f24386c'
NEW = 'modish-backend:build11-20260922'
ARCHIVE_SHA = 'fcaeee0e8101e49f9e4bd3fb9c55382519e23e286660e8341276a7f788d708af'


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def inspect(name):
    return json.loads(subprocess.check_output(['docker', 'inspect', name]))[0]


def guard():
    current = inspect('modish_backend')
    assert current['Config']['Image'] == OLD and current['Image'] == OLD_IMAGE
    assert 'modish_default' in current['NetworkSettings']['Networks']


def verify_image(manifest):
    code = "import json,sys,hashlib;from pathlib import Path;m=json.load(sys.stdin);bad=[n for n,s in m['files'].items() if hashlib.sha256(Path('/app/'+n.removeprefix('backend/')).read_bytes()).hexdigest()!=s];assert not bad,bad;print('source_verified')"
    result = subprocess.check_output(['docker', 'run', '--rm', '-i', '--network', 'none',
        '--entrypoint', 'python', NEW, '-c', code], input=json.dumps(manifest).encode())
    assert b'source_verified' in result


def prepare():
    guard()
    assert hashlib.sha256((R/'source.tar').read_bytes()).hexdigest() == ARCHIVE_SHA
    with tarfile.open(R/'source.tar') as archive:
        archive.extractall(R, filter='data')
    manifest = json.loads((R/'source-manifest.json').read_text())
    assert manifest['base_image'] == OLD_IMAGE
    assert manifest['database_migration'] is False and manifest['catalog_foundation_included'] is False
    assert all(hashlib.sha256((R/name).read_bytes()).hexdigest() == checksum
               for name, checksum in manifest['files'].items())
    with (R/'build.log').open('w') as log:
        run('docker', 'build', '-t', NEW, str(R/'backend'), stdout=log, stderr=subprocess.STDOUT)
    verify_image(manifest)
    print(json.dumps({'progress': 'image_built'}), flush=True)
    with (R/'unit-tests.log').open('w') as log:
        run('docker', 'run', '--rm', '--network', 'none', '-e', 'DATABASE_URL=sqlite:///:memory:',
            '-e', 'JWT_SECRET=isolated-tests-only', '-e', 'PYTHON_DOTENV_DISABLED=1',
            '--entrypoint', 'python', NEW, '-m', 'unittest', 'discover', '-s', 'tests', '-v',
            stdout=log, stderr=subprocess.STDOUT)
    with (R/'smoke.log').open('w') as log:
        run('docker', 'run', '--rm', '--network', 'modish_default', '--env-file', '/opt/Modish/backend/.env',
            '-v', str(R/'smoke.py')+':/smoke.py:ro', '-e', 'PYTHONPATH=/app',
            '--entrypoint', 'python', NEW, '/smoke.py', stdout=log, stderr=subprocess.STDOUT)
    report = [json.loads(line) for line in (R/'smoke.log').read_text().splitlines() if line.startswith('{')][-1]
    assert report['result'] == 'passed' and report['transaction_read_only'] is True
    (R/'smoke-report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False))
    guard()
    (R/'prepared.json').write_text(json.dumps({'image_id': inspect(NEW)['Id'], 'archive_sha256': ARCHIVE_SHA}))
    print(json.dumps({'progress': 'ready_for_activation'}), flush=True)


def activate():
    guard()
    prepared = json.loads((R/'prepared.json').read_text())
    assert prepared == {'image_id': inspect(NEW)['Id'], 'archive_sha256': ARCHIVE_SHA}
    report = json.loads((R/'smoke-report.json').read_text())
    assert report['result'] == 'passed' and report['transaction_read_only'] is True
    unit = (R/'unit-tests.log').read_text()
    assert '\nOK\n' in unit
    unit_count = int(re.search(r'Ran (\d+) tests', unit).group(1))
    assert unit_count >= 128
    manifest = json.loads((R/'source-manifest.json').read_text())
    verify_image(manifest)
    state = {name: inspect(name)['Id'] for name in ('modish_postgres','modish_redis')}
    compose = Path('/opt/Modish/docker-compose.yml')
    original = compose.read_text()
    assert OLD in original and NEW not in original
    B.mkdir(parents=True, exist_ok=True, mode=0o700)
    (B/'docker-compose.yml').write_text(original)
    for kind, tag in [('release', NEW), ('rollback', OLD)]:
        override = {'services': {'backend': {'image': tag, 'pull_policy': 'never',
            'entrypoint': ['python','-m','uvicorn'],
            'command': ['app.main:app','--host','0.0.0.0','--port','8000']}}}
        (R/f'{kind}.compose.json').write_text(json.dumps(override, indent=2))
        script = R/f'{kind}.sh'
        text = '#!/bin/sh\nset -eu\n'
        if kind == 'rollback':
            text += 'python3 - <<\'PY\'\nfrom pathlib import Path\np=Path("/opt/Modish/docker-compose.yml")\np.write_text(p.read_text().replace("'+NEW+'","'+OLD+'"))\nPY\n'
        text += 'docker compose -p modish --env-file /opt/Modish/backend/.env -f /opt/Modish/docker-compose.yml -f '+str(R/f'{kind}.compose.json')+' up -d --no-deps --no-build --pull never backend\n'
        script.write_text(text)
        script.chmod(0o700)
    record = {'started_at': datetime.now(timezone.utc).isoformat(), 'previous_tag': OLD,
        'previous_image': OLD_IMAGE, 'tag': NEW, 'migrations_applied': False,
        'catalog_data_changed': False, 'catalog_foundation_included': False,
        'archive_sha256': ARCHIVE_SHA, 'unit_tests': unit_count, 'checks': []}
    attempted = False
    try:
        env = dict(value.split('=',1) for value in inspect('modish_postgres')['Config']['Env'] if '=' in value)
        with (B/'pre-activation.dump').open('wb') as dump:
            run('docker','exec','modish_postgres','pg_dump','-U',env['POSTGRES_USER'],
                '-d',env['POSTGRES_DB'],'-Fc',stdout=dump)
        assert (B/'pre-activation.dump').stat().st_size > 1000
        attempted = True
        with (R/'compose-activation.log').open('w') as log:
            run(str(R/'release.sh'), stdout=log, stderr=subprocess.STDOUT)
        for _ in range(40):
            try:
                with urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3) as response:
                    if json.load(response).get('database') == 'ok': break
            except Exception: pass
            time.sleep(1)
        else: raise RuntimeError('API health failed')
        for base in ('http://127.0.0.1:8000','https://modish.org.ru'):
            for route in ('/health','/openapi.json','/products?'+urllib.parse.urlencode({'q':'куртка','limit':3}),
                          '/products?q=modish-no-such-item-9283716'):
                with urllib.request.urlopen(base+route, timeout=30) as response:
                    assert response.status == 200
                    data = json.load(response)
                if route == '/openapi.json':
                    assert any(p['name'] == 'q' for p in data['paths']['/products']['get']['parameters'])
                elif route.endswith('9283716'): assert data == []
                elif route.startswith('/products?'):
                    assert data and all('куртк' in ' '.join(str(p.get(k) or '') for k in
                        ('title','category','category_name','subcategory')).lower() for p in data)
                record['checks'].append({'route':base+route, 'status':200})
        assert all(inspect(name)['Id'] == cid for name,cid in state.items())
        compose.write_text(original.replace(OLD,NEW))
        record.update(status='deployed', image_id=inspect('modish_backend')['Image'],
            database_and_redis_containers_preserved=True, backup=str(B/'pre-activation.dump'),
            rollback=str(R/'rollback.sh'), finished_at=datetime.now(timezone.utc).isoformat())
        Path('/opt/Modish/DEPLOYMENT_CURRENT.md').write_text('# Current release\n\n'+str(R)+
            '\nImage: '+NEW+'\nRollback: '+str(R/'rollback.sh')+
            '\nCatalogue search and weather-compatible complements. No migrations or catalogue rewrites.\n')
    except Exception:
        compose.write_text(original)
        if attempted: run(str(R/'rollback.sh'), stdout=subprocess.DEVNULL)
        record['status'] = 'rolled_back'
        raise
    finally:
        (R/'deployment-record.json').write_text(json.dumps(record, indent=2))
    print(json.dumps(record), flush=True)


if __name__ == '__main__':
    os.umask(0o077)
    assert sys.argv[1] in {'prepare','activate'}
    (prepare if sys.argv[1] == 'prepare' else activate)()
