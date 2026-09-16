"""Activate only the tested styling API image, with backup and automatic rollback."""
from datetime import datetime, timezone
import json, subprocess, time, urllib.request, hashlib, os
from pathlib import Path
R=Path('/opt/modish-releases/20260916-release7')
B=Path('/var/backups/modish/20260916-release7')
OLD='modish-backend:release6-20260915'
NEW='modish-backend:release7-20260916'
def run(*a,**kw):return subprocess.run(list(a),check=True,**kw)
def inspect(n):return json.loads(subprocess.check_output(['docker','inspect',n]))[0]
def main():
    os.umask(0o077)
    report=[json.loads(x) for x in (R/'smoke.log').read_text().splitlines() if x.startswith('{')][-1]
    assert report['result']=='passed' and len(report['checks'])==6
    assert all(row['refreshes']==3 and row['distinct_batches']==3 for row in report['checks'])
    assert report['concurrent_refreshes']==2
    unit=(R/'unit-tests.log').read_text();assert 'Ran 105 tests' in unit and '\nOK\n' in unit
    old=inspect('modish_backend');assert old['Config']['Image']==OLD
    assert old['Image']=='sha256:4c90e3b230343ded22da88c6c18e2f94243c5ad965b188731fee7a12750b7d83'
    state={n:inspect(n)['Id'] for n in ('modish_postgres','modish_redis')}
    manifest=json.loads((R/'source-manifest.json').read_text())
    verifier="import sys,json,hashlib;from pathlib import Path;m=json.load(sys.stdin);bad=[n for n,s in m['files'].items() if hashlib.sha256(Path('/app/'+n.removeprefix('backend/')).read_bytes()).hexdigest()!=s];assert not bad,bad;print('source_verified')"
    checked=subprocess.check_output(['docker','run','--rm','-i','--network','none','--entrypoint','python',NEW,'-c',verifier],input=json.dumps(manifest).encode())
    assert b'source_verified' in checked
    compose=Path('/opt/Modish/docker-compose.yml');original=compose.read_text();assert OLD in original
    for kind,tag in [('release',NEW),('rollback',OLD)]:
        override={'services':{'backend':{'image':tag,'pull_policy':'never','entrypoint':['python','-m','uvicorn'],
            'command':['app.main:app','--host','0.0.0.0','--port','8000']}}}
        (R/f'{kind}.compose.json').write_text(json.dumps(override,indent=2))
        script=R/f'{kind}.sh'
        script.write_text('#!/bin/sh\nset -eu\ndocker compose -p modish --env-file /opt/Modish/backend/.env -f /opt/Modish/docker-compose.yml -f '+str(R/f'{kind}.compose.json')+' up -d --no-deps --no-build --pull never backend\n')
        script.chmod(0o700)
    record={'started_at':datetime.now(timezone.utc).isoformat(),'previous_tag':OLD,'previous_image':old['Image'],
        'tag':NEW,'migrations_applied':False,'source_files':len(manifest['files']),'catalog_data_changed':False}
    activation_attempted=False
    try:
        pg=inspect('modish_postgres');env=dict(x.split('=',1) for x in pg['Config']['Env'] if '=' in x)
        with (B/'pre-activation.dump').open('wb') as dump:
            run('docker','exec','modish_postgres','pg_dump','-U',env['POSTGRES_USER'],'-d',env['POSTGRES_DB'],'-Fc',stdout=dump)
        activation_attempted=True
        with (R/'compose-activation.log').open('w') as log:run(str(R/'release.sh'),stdout=log,stderr=subprocess.STDOUT)
        for _ in range(40):
            try:
                with urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=3) as res:health=json.load(res)
                if health.get('database')=='ok':break
            except Exception:pass
            time.sleep(1)
        else:raise RuntimeError('health_failed')
        checks=[]
        for base in ('http://127.0.0.1:8000','https://modish.org.ru'):
            for route in ('/health','/products?limit=2','/openapi.json'):
                with urllib.request.urlopen(base+route,timeout=20) as res:
                    assert res.status==200
                    data=json.load(res)
                if route=='/openapi.json':assert '/auth/refresh' in data['paths'] and '/auth/session' in data['paths']
                checks.append({'url':base+route,'status':200})
        assert all(inspect(n)['Id']==cid for n,cid in state.items())
        compose.write_text(original.replace(OLD,NEW))
        record.update(status='deployed',image_id=inspect('modish_backend')['Image'],unit_tests=105,
            checks=checks,isolated_checks=report['checks'],finished_at=datetime.now(timezone.utc).isoformat())
        Path('/opt/Modish/DEPLOYMENT_CURRENT.md').write_text('# Current release\n\n'+str(R)+'\n\nImage: '+NEW+
            '\nSource: baseline '+manifest['baseline']+' plus source-manifest.json.\nDeploy: '+str(R/'release.sh')+
            '\nRollback: '+str(R/'rollback.sh')+'\nOutfit rotation update; no schema changes or catalog repairs.\n'+
            'Previous gender repair journal: /var/backups/modish/20260913-catalog-fixes/gender-journal.jsonl\n'+
            'Do not build the old /opt/Modish/backend checkout.\n')
    except Exception:
        compose.write_text(original)
        if activation_attempted:run(str(R/'rollback.sh'),stdout=subprocess.DEVNULL)
        record['status']='rolled_back'
        raise
    finally:
        (R/'deployment-record.json').write_text(json.dumps(record,indent=2))
    print(json.dumps(record),flush=True)
if __name__=='__main__':main()
