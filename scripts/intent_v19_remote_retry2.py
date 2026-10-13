"""Retry only the corrected PostgreSQL harness; no application code changes."""
from datetime import datetime, timezone
import hashlib, json, os, subprocess, tarfile
from pathlib import Path
from urllib.parse import urlsplit
R=Path('/opt/modish-releases/20261009-intent-v19')
BASE='sha256:4ae234fc8a1f8082bb368f4f617d187a6138bb5848c02af5e9a7a9f15a3d6d7e'
SHA='46fb7ba5335db45f563884a6f90a0496abf262b79a6c8c12e7bd7fca2af3e727'
def inspect(name): return json.loads(subprocess.check_output(['docker','inspect',name]))[0]
state={n:inspect(n)['Id'] for n in ('modish_backend','modish_postgres','modish_redis')}
assert inspect('modish_backend')['Image']==BASE
assert hashlib.sha256((R/'source.tar').read_bytes()).hexdigest()==SHA
assert not (R/'stage-report.json').exists()
assert json.loads(subprocess.check_output(['docker','network','inspect','modish-foundation-test']))[0]['Internal']
env=Path('/opt/modish-releases/20260922-foundation/test-api.env')
values=dict(line.split('=',1) for line in env.read_text().splitlines() if '=' in line and not line.startswith('#'))
u=urlsplit(values['DATABASE_URL'].replace('postgresql+psycopg://','postgresql://'))
assert u.hostname=='modish_foundation_test_pg' and u.path=='/modish_test' and u.username=='modish_test'
image=inspect('modish-backend:intent-v19-20261009')['Id']
with tarfile.open(R/'source.tar') as archive: manifest=json.load(archive.extractfile('source-manifest.json'))
assert manifest['base_image']==BASE
verify="import json,sys,hashlib;from pathlib import Path;m=json.load(sys.stdin);assert all(hashlib.sha256(Path('/app/'+n.removeprefix('backend/')).read_bytes()).hexdigest()==s for n,s in m['files'].items())"
subprocess.run(['docker','run','--rm','-i','--network','none','--entrypoint','python',image,'-c',verify],input=json.dumps(manifest).encode(),check=True)
lines=(R/'unit-tests.log').read_text().splitlines()
assert 'OK' in lines and any(s.startswith('Ran 284 tests') for s in lines)
with (R/'metrics-retry2-report.json').open('w') as output,(R/'metrics-retry2-errors.log').open('w') as error:
 subprocess.run(['docker','run','--rm','--cpus=0.75','--memory=768m','--network','modish-foundation-test',
  '--env-file',str(env),'-e','AUDIT_IMAGE_ID='+image,'-v',str(R/'metrics-smoke-retry2.py')+':/smoke.py:ro',
  '--entrypoint','python',image,'/smoke.py'],stdout=output,stderr=error,check=True,timeout=180)
metrics=json.loads((R/'metrics-retry2-report.json').read_text())
assert metrics['checks_passed'] and metrics['task_only_taste_unchanged'] and metrics['replacement_respects_rejected_skus']
assert all(inspect(n)['Id']==v for n,v in state.items()) and inspect('modish_backend')['Image']==BASE
report={'checked_at':datetime.now(timezone.utc).isoformat(),'scope':'isolated_clone','production_changed':False,
 'archive_sha256':SHA,'image_id':image,'test_revision':'20261008_0024','migration_roundtrip':'not_repeated_no_migration_change',
 'positive_pg_metrics_passed':True,'performance_reference':'/opt/modish-releases/20261009-intent-v18/catalog-report.json',
 'harness_retry':'Explicit flush at simulated API boundaries and fixtures recreated after each savepoint rollback; no test commits',
 'production_unchanged':True,'product_ready':False,'result':'latency_and_metrics_checked_catalogue_pending'}
(R/'stage-report.json').write_text(json.dumps(report,indent=2)+'\n')
print('stage_complete_after_harness_retry',flush=True)
