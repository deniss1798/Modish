"""Build and test a fixed release on an isolated database; no live activation."""
from pathlib import Path
import subprocess, json, os, secrets, time, hashlib, tarfile, shutil
from urllib.parse import quote
R=Path('/opt/modish-releases/20260913-styling-fixes')
B=Path('/var/backups/modish/20260913-styling-fixes')
TAG='modish-backend:styling-fixes-20260913'
NETWORK='modish-styling-test'
PG='modish_styling_test_pg'

def run(*args,**kw):return subprocess.run(list(args),check=True,**kw)
def inspect(name):return json.loads(subprocess.check_output(['docker','inspect',name]))[0]
def main():
    os.umask(0o077)
    assert shutil.disk_usage('/opt').free>2_000_000_000, 'insufficient_disk'
    old=inspect('modish_backend');assert old['Config']['Image']=='modish-backend:catalog-fixes-20260913'
    state={n:inspect(n)['Id'] for n in ('modish_postgres','modish_redis')}
    # Only this fixed test namespace may be created, never replace an existing resource.
    assert subprocess.run(['docker','inspect',PG],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode!=0
    assert subprocess.run(['docker','network','inspect',NETWORK],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode!=0
    assert not (R/'backend').exists()
    with tarfile.open(R/'source.tar') as archive:archive.extractall(R,filter='data')
    manifest=json.loads((R/'source-manifest.json').read_text())
    assert all(hashlib.sha256((R/name).read_bytes()).hexdigest()==sha for name,sha in manifest['files'].items())
    with (R/'build.log').open('w') as log:run('docker','build','-t',TAG,str(R/'backend'),stdout=log,stderr=subprocess.STDOUT)
    print(json.dumps({'progress':'image_built','files':len(manifest['files'])}),flush=True)
    with (R/'unit-tests.log').open('w') as log:
        run('docker','run','--rm','--network','none','-e','DATABASE_URL=sqlite:///:memory:',
            '-e','JWT_SECRET=isolated-tests-only','-e','PYTHON_DOTENV_DISABLED=1','--entrypoint','python',TAG,
            '-m','unittest','discover','-s','tests','-v',stdout=log,stderr=subprocess.STDOUT)
    print(json.dumps({'progress':'unit_tests_passed'}),flush=True)
    B.mkdir(mode=0o700,parents=True,exist_ok=False)
    pg=inspect('modish_postgres');pgenv=dict(x.split('=',1) for x in pg['Config']['Env'] if '=' in x)
    shutil.copy2('/opt/Modish/docker-compose.yml',B/'docker-compose.before.yml')
    with (B/'database.dump').open('wb') as dump:
        run('docker','exec','modish_postgres','pg_dump','-U',pgenv['POSTGRES_USER'],'-d',pgenv['POSTGRES_DB'],'-Fc',stdout=dump)
    password=secrets.token_urlsafe(32)
    (R/'test-postgres.env').write_text(f'POSTGRES_USER=modish_test\nPOSTGRES_DB=modish_test\nPOSTGRES_PASSWORD={password}\n')
    env=dict(x.split('=',1) for x in old['Config']['Env'] if '=' in x)
    env.update(DATABASE_URL=f'postgresql+psycopg://modish_test:{quote(password,safe="")}@{PG}:5432/modish_test',
        JWT_SECRET=secrets.token_urlsafe(48), REDIS_URL='redis://127.0.0.1:6379/15', RATE_LIMIT_PER_MINUTE='100000',
        ADMITAD_ENABLED='false', OPENAI_API_KEY='', OPENROUTER_API_KEY='', PYTHONPATH='/app')
    (R/'test-api.env').write_text('\n'.join(f'{k}={v}' for k,v in env.items())+'\n')
    run('docker','network','create',NETWORK,stdout=subprocess.DEVNULL)
    run('docker','run','-d','--name',PG,'--network',NETWORK,'--env-file',str(R/'test-postgres.env'),'postgres:16',stdout=subprocess.DEVNULL)
    for _ in range(45):
        if subprocess.run(['docker','exec',PG,'pg_isready','-U','modish_test'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0:break
        time.sleep(1)
    else:raise RuntimeError('isolated_database_not_ready')
    with (B/'database.dump').open('rb') as dump, (R/'restore.log').open('w') as log:
        run('docker','exec','-i',PG,'pg_restore','--no-owner','--no-acl','--exit-on-error','-U','modish_test','-d','modish_test',stdin=dump,stdout=log,stderr=subprocess.STDOUT)
    assert all(inspect(n)['Id']==cid for n,cid in state.items())
    print(json.dumps({'progress':'isolated_restore_ready','backup_bytes':(B/'database.dump').stat().st_size}),flush=True)
    with (R/'smoke.log').open('w') as log:
        run('docker','run','--rm','--name','modish_styling_smoke','--network',NETWORK,'--env-file',str(R/'test-api.env'),
            '-v',str(R/'smoke_styling_fixes.py')+':/smoke.py:ro','--entrypoint','python',TAG,'/smoke.py',stdout=log,stderr=subprocess.STDOUT)
    assert all(inspect(n)['Id']==cid for n,cid in state.items())
    print(json.dumps({'progress':'ready_for_activation'}),flush=True)

if __name__=='__main__':main()
