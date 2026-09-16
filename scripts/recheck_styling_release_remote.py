"""Refresh only the unactivated styling image and rerun tests on its existing isolated DB."""
import json, subprocess, tarfile, hashlib
from pathlib import Path
R=Path('/opt/modish-releases/20260913-styling-fixes')
TAG='modish-backend:styling-fixes-20260913'
def run(*a,**k):return subprocess.run(list(a),check=True,**k)
def inspect(n):return json.loads(subprocess.check_output(['docker','inspect',n]))[0]
assert inspect('modish_backend')['Config']['Image']=='modish-backend:catalog-fixes-20260913'
assert set(inspect('modish_styling_test_pg')['NetworkSettings']['Networks'])=={'modish-styling-test'}
assert subprocess.run(['docker','inspect','modish_styling_smoke'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode!=0
state={n:inspect(n)['Id'] for n in ('modish_backend','modish_postgres','modish_redis')}
with tarfile.open(R/'source.tar') as archive:archive.extractall(R,filter='data')
manifest=json.loads((R/'source-manifest.json').read_text())
assert all(hashlib.sha256((R/name).read_bytes()).hexdigest()==sha for name,sha in manifest['files'].items())
with (R/'build.log').open('w') as log:run('docker','build','-t',TAG,str(R/'backend'),stdout=log,stderr=subprocess.STDOUT)
verifier="import sys,json,hashlib;from pathlib import Path;m=json.load(sys.stdin);bad=[n for n,s in m['files'].items() if hashlib.sha256(Path('/app/'+n.removeprefix('backend/')).read_bytes()).hexdigest()!=s];assert not bad,bad"
run('docker','run','--rm','-i','--network','none','--entrypoint','python',TAG,'-c',verifier,input=json.dumps(manifest).encode())
with (R/'unit-tests.log').open('w') as log:
 run('docker','run','--rm','--network','none','-e','DATABASE_URL=sqlite:///:memory:','-e','JWT_SECRET=isolated-tests-only',
  '-e','PYTHON_DOTENV_DISABLED=1','--entrypoint','python',TAG,'-m','unittest','discover','-s','tests','-v',stdout=log,stderr=subprocess.STDOUT)
print(json.dumps({'progress':'final_image_and_units_passed'}),flush=True)
with (R/'smoke.log').open('w') as log:
 run('docker','run','--rm','--name','modish_styling_smoke','--network','modish-styling-test','--env-file',str(R/'test-api.env'),
  '-v',str(R/'smoke_styling_fixes.py')+':/smoke.py:ro','--entrypoint','python',TAG,'/smoke.py',stdout=log,stderr=subprocess.STDOUT)
assert all(inspect(n)['Id']==cid for n,cid in state.items())
print(json.dumps({'progress':'ready_for_activation'}),flush=True)
