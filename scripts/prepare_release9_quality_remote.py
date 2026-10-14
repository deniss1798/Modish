"""Rebuild reviewed files and rerun checks in the existing isolated clone."""
from pathlib import Path
import subprocess,json,tarfile,hashlib
R=Path('/opt/modish-releases/20260920-release9-quality')
TAG='modish-backend:release9-quality-20260920'
def run(*args,**kw): return subprocess.run(list(args),check=True,**kw)
def inspect(name):return json.loads(subprocess.check_output(['docker','inspect',name]))[0]
def main():
    assert inspect('modish_backend')['Image']=='sha256:367fc511e3a521f576f12be39fec42cc200d49c60550c4a8b6b3f0683241ecdc'
    assert set(inspect('modish_release9_test_pg')['NetworkSettings']['Networks'])=={'modish-release9-test'}
    assert not inspect('modish_release9_test_pg')['HostConfig']['Binds']
    with tarfile.open(R/'source.tar') as archive:archive.extractall(R,filter='data')
    manifest=json.loads((R/'source-manifest.json').read_text())
    assert all(hashlib.sha256((R/n).read_bytes()).hexdigest()==sha for n,sha in manifest['files'].items())
    with (R/'build.log').open('w') as log:run('docker','build','-t',TAG,str(R/'backend'),stdout=log,stderr=subprocess.STDOUT)
    with (R/'unit-tests.log').open('w') as log:
        run('docker','run','--rm','--network','none','-e','DATABASE_URL=sqlite:///:memory:','-e','JWT_SECRET=SYNTHETIC_ISOLATED_JWT',
            '-e','PYTHON_DOTENV_DISABLED=1','--entrypoint','python',TAG,'-m','unittest','discover','-s','tests','-v',stdout=log,stderr=subprocess.STDOUT)
    with (R/'smoke.log').open('w') as log:
        run('docker','run','--rm','--name','modish_release9_smoke','--network','modish-release9-test','--env-file','/opt/modish-releases/20260917-release9/test-api.env',
            '-v',str(R/'smoke_release9.py')+':/smoke.py:ro','--entrypoint','python',TAG,'/smoke.py',stdout=log,stderr=subprocess.STDOUT)
    print(json.dumps({'progress':'ready_for_activation'}),flush=True)
if __name__=='__main__':main()
