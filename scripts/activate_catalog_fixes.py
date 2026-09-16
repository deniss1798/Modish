"""Activate the verified catalog release; preserve the live database and Redis."""
from datetime import datetime,timezone
import json,subprocess,time,shutil,urllib.request
from pathlib import Path
R=Path('/opt/modish-releases/20260913-catalog-fixes')
B=Path('/var/backups/modish/20260913-catalog-fixes')
OLD='modish-backend:outfit-fixes-20260913'
NEW='modish-backend:catalog-fixes-20260913'

def run(*args,**kwargs):return subprocess.run(list(args),check=True,**kwargs)
def inspect(name):return json.loads(subprocess.check_output(['docker','inspect',name]))[0]
def main():
    report=[json.loads(x) for x in (R/'catalog-smoke-final.log').read_text().splitlines() if x.startswith('{')][-1]
    assert report['result']=='passed' and len(report['checks'])==10
    unit=(R/'unit-tests-final.log').read_text();assert 'Ran 87 tests' in unit and '\nOK\n' in unit
    old=inspect('modish_backend');assert old['Config']['Image']==OLD
    state={n:inspect(n)['Id'] for n in ('modish_postgres','modish_redis')}
    source_manifest=json.loads((R/'source-manifest.json').read_text())
    compose=Path('/opt/Modish/docker-compose.yml');original=compose.read_text();assert OLD in original
    env=R/'runtime.env';env.write_text('\n'.join(old['Config']['Env'])+'\n');env.chmod(0o600)
    for kind,tag in [('release',NEW),('rollback',OLD)]:
        override={'services':{'backend':{'image':tag,'pull_policy':'never','entrypoint':['python','-m','uvicorn'],'command':['app.main:app','--host','0.0.0.0','--port','8000']}}}
        (R/f'{kind}.compose.json').write_text(json.dumps(override,indent=2))
        (R/f'{kind}.sh').write_text('#!/bin/sh\nset -eu\ndocker compose -p modish --env-file /opt/Modish/backend/.env -f /opt/Modish/docker-compose.yml -f '+str(R/f'{kind}.compose.json')+' up -d --no-deps --no-build --pull never backend\n');(R/f'{kind}.sh').chmod(0o700)
    record={'started_at':datetime.now(timezone.utc).isoformat(),'previous_image':old['Image'],'previous_tag':OLD,'tag':NEW,'migrations_applied':False,'source_files':len(source_manifest['files'])}
    repaired=False;activated=False
    try:
        pg=inspect('modish_postgres');pgenv=dict(x.split('=',1) for x in pg['Config']['Env'] if '=' in x)
        with (B/'pre-activation.dump').open('wb') as dump:
            (B/'pre-activation.dump').chmod(0o600)
            run('docker','exec','modish_postgres','pg_dump','-U',pgenv['POSTGRES_USER'],'-d',pgenv['POSTGRES_DB'],'-Fc',stdout=dump)
        repair_args=['docker','run','--rm','--name','modish_catalog_repair','--network','modish_default','--env-file',str(env),'-e','PYTHONPATH=/app','-v',str(B)+':/repair','--entrypoint','python',NEW,'scripts/repair_catalog_gender.py','--journal','/repair/gender-journal.jsonl']
        with (R/'production-repair.log').open('w') as out:run(*repair_args,'--apply',stdout=out,stderr=subprocess.STDOUT)
        repaired=True
        repair=json.loads((R/'production-repair.log').read_text().splitlines()[-1]);assert repair['result']=='applied'
        record['catalog_repair']=repair
        with (R/'activation.log').open('w') as out:run(str(R/'release.sh'),stdout=out,stderr=subprocess.STDOUT)
        activated=True
        for _ in range(30):
            try:
                with urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=3) as res:health=json.load(res)
                if health.get('database')=='ok':break
            except Exception:pass
            time.sleep(1)
        else:raise RuntimeError('health_failed')
        checks=[]
        for base in ('http://127.0.0.1:8000','https://modish.org.ru'):
            for route in ('/health','/products?limit=1'):
                with urllib.request.urlopen(base+route,timeout=15) as res:assert res.status==200
                checks.append({'url':base+route,'status':200})
        assert all(inspect(n)['Id']==cid for n,cid in state.items()),'database_container_changed'
        compose.write_text(original.replace(OLD,NEW))
        record.update(status='deployed',image_id=inspect('modish_backend')['Image'],checks=checks,isolated_checks=report['checks'],image_probes=len(report['images']),unit_tests=87,finished_at=datetime.now(timezone.utc).isoformat())
        Path('/opt/Modish/DEPLOYMENT_CURRENT.md').write_text('# Current release\n\n'+str(R)+'\n\nImage: '+NEW+'\n\nSource: baseline '+source_manifest['baseline']+' plus source-manifest.json.\n\nDeploy: '+str(R/'release.sh')+'\nRollback API: '+str(R/'rollback.sh')+'\n\nCatalog gender repair journal: '+str(B/'gender-journal.jsonl')+'. The release repair script supports --rollback.\nDo not rebuild the old /opt/Modish/backend checkout.\n')
    except Exception:
        compose.write_text(original)
        if activated:run(str(R/'rollback.sh'),stdout=subprocess.DEVNULL)
        if repaired:run(*repair_args,'--rollback',stdout=subprocess.DEVNULL)
        record['status']='rolled_back'
        raise
    finally:
        env.unlink(missing_ok=True)
        (R/'deployment-record.json').write_text(json.dumps(record,indent=2))
    print(json.dumps(record))

if __name__=='__main__':main()
