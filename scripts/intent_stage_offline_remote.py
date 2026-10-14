"""Verified immutable package, PostgreSQL roundtrip and real clone audit.

Only the existing internal test database can be migrated. Production containers
are inspected for stability and are never stopped, restarted, or modified.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

R=Path('/opt/modish-releases/20261008-intent-v14')
TEST_ENV=Path('/opt/modish-releases/20260922-foundation/test-api.env')
PG='modish_foundation_test_pg'
NETWORK='modish-foundation-test'
BASE='sha256:4ae234fc8a1f8082bb368f4f617d187a6138bb5848c02af5e9a7a9f15a3d6d7e'
TAG='modish-backend:intent-v14-20261008'
EXPECTED_REVISION='20260921_0022'
TARGET_REVISION='20261008_0024'
os.umask(0o077)

def inspect(name):
    return json.loads(subprocess.check_output(['docker','inspect',name]))[0]

def run(*args,**kwargs):
    return subprocess.run(args,check=True,**kwargs)

def db_query(sql):
    return subprocess.check_output(['docker','exec',PG,'psql','-U','modish_test','-d','modish_test','-Atc',sql],text=True).strip()


def main():
    report={'checked_at':datetime.now(timezone.utc).isoformat(),'scope':'isolated_clone',
        'production_changed':False,'result':'running'}
    state={name:inspect(name)['Id'] for name in ('modish_backend','modish_postgres','modish_redis')}
    assert inspect('modish_backend')['Image']==BASE
    assert inspect(PG)['State']['Running']
    network=json.loads(subprocess.check_output(['docker','network','inspect',NETWORK]))[0]
    assert network['Internal']
    assert shutil.disk_usage(R).free>3_000_000_000
    # Never print any environment values. Guard both database destination and
    # internal test network before any migration command can run.
    values=dict(line.split('=',1) for line in TEST_ENV.read_text().splitlines() if '=' in line and not line.startswith('#'))
    from urllib.parse import urlsplit
    url=urlsplit(values['DATABASE_URL'].replace('postgresql+psycopg://','postgresql://'))
    assert url.hostname==PG and url.path=='/modish_test' and url.username=='modish_test'
    revision=db_query('SELECT version_num FROM alembic_version')
    assert revision==EXPECTED_REVISION, 'Unexpected test database revision'
    archive_sha=hashlib.sha256((R/'source.tar').read_bytes()).hexdigest()
    assert archive_sha==sys.argv[1]
    report['archive_sha256']=archive_sha
    with tarfile.open(R/'source.tar') as archive:
        manifest=json.load(archive.extractfile('source-manifest.json'))
        assert manifest['base_image']==BASE and manifest['migration']==TARGET_REVISION
        for name,sha in manifest['files'].items():
            member=archive.getmember(name)
            target=(R/name).resolve()
            assert target.is_relative_to(R.resolve()) and member.isfile()
            data=archive.extractfile(member).read()
            assert hashlib.sha256(data).hexdigest()==sha
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(data)
    # The Python libraries are already present in the pinned deployed image.
    # Verify every required version before reusing those layers, without pip or network.
    verify_dependencies="from importlib.metadata import version;from pip._vendor.packaging.requirements import Requirement;from pathlib import Path;requirements=[Requirement(s) for s in Path('/check-requirements.txt').read_text().splitlines() if s.strip() and not s.startswith('#')];assert all(version(r.name) in r.specifier for r in requirements);assert not Path('/app/.env').exists();print('image_dependencies_verified')"
    run('docker','run','--rm','--network','none','-v',str(R/'backend/requirements.txt')+':/check-requirements.txt:ro',
        '--entrypoint','python',BASE,'-c',verify_dependencies)
    dependencies_tag='modish-backend:intent-v14-dependencies-20261008'
    run('docker','tag',BASE,dependencies_tag)
    assert inspect(dependencies_tag)['Id']==BASE
    dockerfile=R/'Offline.Dockerfile'
    dockerfile.write_text('FROM '+dependencies_tag+'\nWORKDIR /app\nCOPY . .\nENV PYTHONUNBUFFERED=1\nCMD ["scripts/entrypoint.sh"]\n')
    with (R/'build-offline.log').open('w') as log:
        run('docker','build','--pull=false','--network=none','-f',str(dockerfile),'-t',TAG,str(R/'backend'),stdout=log,stderr=subprocess.STDOUT)
    assert inspect(dependencies_tag)['Id']==BASE
    image=inspect(TAG)['Id'];report['image_id']=image
    print('image_built',flush=True)
    verify="import json,sys,hashlib;from pathlib import Path;m=json.load(sys.stdin);bad=[n for n,s in m['files'].items() if hashlib.sha256(Path('/app/'+n.removeprefix('backend/')).read_bytes()).hexdigest()!=s];assert not bad,bad"
    run('docker','run','--rm','-i','--network','none','--entrypoint','python',image,'-c',verify,input=json.dumps(manifest).encode())
    print('image_sources_verified',flush=True)
    with (R/'unit-tests.log').open('w') as log:
        run('docker','run','--rm','--network','none','-e','DATABASE_URL=sqlite:///:memory:',
            '-e','JWT_SECRET=SYNTHETIC_ISOLATED_JWT','-e','PYTHON_DOTENV_DISABLED=1','--entrypoint','python',
            image,'-m','unittest','discover','-s','tests','-v',stdout=log,stderr=subprocess.STDOUT)
    print('unit_tests_passed',flush=True)
    count=db_query('SELECT count(*) FROM products')
    with (R/'test-before-migration.dump').open('wb') as dump:
        run('docker','exec',PG,'pg_dump','-U','modish_test','-d','modish_test','-Fc',stdout=dump)
    report['test_backup']=str(R/'test-before-migration.dump')
    with (R/'migration-test.log').open('w') as log:
        for direction,revision in [('upgrade',TARGET_REVISION),('downgrade',EXPECTED_REVISION),('upgrade',TARGET_REVISION)]:
            run('docker','run','--rm','--network',NETWORK,'--env-file',str(TEST_ENV),
                '--entrypoint','alembic',image,direction,revision,stdout=log,stderr=subprocess.STDOUT)
            assert db_query('SELECT count(*) FROM products')==count
            assert db_query('SELECT version_num FROM alembic_version')==revision
    report['migration_roundtrip']=True;report['test_revision']=TARGET_REVISION
    print('migration_roundtrip_passed',flush=True)
    with (R/'catalog-report.json').open('w') as output,(R/'catalog-errors.log').open('w') as error:
        run('docker','run','--rm','--network',NETWORK,'--env-file',str(TEST_ENV),
            '-e','AUDIT_ARCHIVE_SHA256='+archive_sha,'-e','AUDIT_IMAGE_ID='+image,
            '-v',str(R/'catalog-audit.py')+':/audit.py:ro','--entrypoint','python',image,'/audit.py',stdout=output,stderr=error)
    catalog=json.loads((R/'catalog-report.json').read_text())
    assert catalog['checks_passed']
    assert all(inspect(name)['Id']==container_id for name,container_id in state.items())
    assert inspect('modish_backend')['Image']==BASE
    report['production_unchanged']=True
    report['product_ready']=catalog['product_ready']
    report['result']='checks_passed' if catalog['product_ready'] else 'catalogue_coverage_failed'
    (R/'stage-report.json').write_text(json.dumps(report,indent=2)+'\n')
    print('stage_complete '+report['result'],flush=True)

if __name__=='__main__':
    main()
