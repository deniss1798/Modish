"""Retest the three changed classification files; retain evidence for unchanged feed concurrency code."""
import json,subprocess,tarfile,hashlib
from pathlib import Path
R=Path('/opt/modish-releases/20260913-styling-fixes');TAG='modish-backend:styling-fixes-20260913'
def run(*a,**k):return subprocess.run(list(a),check=True,**k)
def inspect(n):return json.loads(subprocess.check_output(['docker','inspect',n]))[0]
assert inspect('modish_backend')['Config']['Image']=='modish-backend:catalog-fixes-20260913'
assert set(inspect('modish_styling_test_pg')['NetworkSettings']['Networks'])=={'modish-styling-test'}
old_manifest=json.loads((R/'source-manifest.json').read_text())
smoke=[json.loads(x) for x in (R/'smoke.log').read_text().splitlines() if x.startswith('{')][-1]
assert smoke['result']=='passed'
with tarfile.open(R/'source.tar') as archive:
 manifest=json.load(archive.extractfile('source-manifest.json'))
 changed={p for p,s in manifest['files'].items() if old_manifest['files'].get(p)!=s}
 assert changed<= {'backend/app/services/garment_roles.py','backend/app/services/outfit_quality.py','backend/tests/test_styling_regressions.py'},changed
 archive.extractall(R,filter='data')
prior={'smoke':smoke,'image_id':inspect(TAG)['Id'],'changed_files':sorted(changed)}
(R/'prior-long-smoke.json').write_text(json.dumps(prior))
with (R/'build.log').open('w') as log:run('docker','build','-t',TAG,str(R/'backend'),stdout=log,stderr=subprocess.STDOUT)
verifier="import sys,json,hashlib;from pathlib import Path;m=json.load(sys.stdin);bad=[n for n,s in m['files'].items() if hashlib.sha256(Path('/app/'+n.removeprefix('backend/')).read_bytes()).hexdigest()!=s];assert not bad,bad"
run('docker','run','--rm','-i','--network','none','--entrypoint','python',TAG,'-c',verifier,input=json.dumps(manifest).encode())
with (R/'unit-tests.log').open('w') as log:
 run('docker','run','--rm','--network','none','-e','DATABASE_URL=sqlite:///:memory:','-e','JWT_SECRET=isolated-tests-only',
  '-e','PYTHON_DOTENV_DISABLED=1','--entrypoint','python',TAG,'-m','unittest','discover','-s','tests','-v',stdout=log,stderr=subprocess.STDOUT)
with (R/'smoke.log').open('w') as log:
 run('docker','run','--rm','--name','modish_styling_smoke','--network','modish-styling-test','--env-file',str(R/'test-api.env'),
  '-v',str(R/'smoke_styling_quality.py')+':/quality.py:ro','-v',str(R/'prior-long-smoke.json')+':/prior.json:ro',
  '--entrypoint','python',TAG,'/quality.py',stdout=log,stderr=subprocess.STDOUT)
print(json.dumps({'progress':'ready_for_activation'}),flush=True)
