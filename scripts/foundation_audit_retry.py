"""Replace only a running read-only audit with the equivalent faster profiler."""
from pathlib import Path
import json
import subprocess

R = Path('/opt/modish-releases/20260922-foundation')
TAG = 'modish-backend:foundation-20260922'
def inspect(name):
    return json.loads(subprocess.check_output(['docker','inspect',name]))[0]
assert inspect('modish_backend')['Image'] == 'sha256:420c4ff4467bd995cfd11f9598c0f90988e79c1e4faf33d004f11f69c87c201f'
assert '\nOK\n' in (R/'unit-tests.log').read_text()
for cid in subprocess.check_output(['docker','ps','-q','--filter','ancestor='+TAG]).decode().split():
    info = inspect(cid)
    assert info['Config']['Image'] == TAG and info['Args'] == ['/audit.py']
    subprocess.run(['docker','stop','-t','5',cid],check=True,stdout=subprocess.DEVNULL)
with (R/'audit-report.json').open('w') as report, (R/'audit-errors.log').open('w') as errors:
    subprocess.run(['docker','run','--rm','--network','modish_default','--env-file','/opt/Modish/backend/.env',
        '-v',str(R/'audit.py')+':/audit.py:ro','-e','PYTHONPATH=/app','--entrypoint','python',TAG,'/audit.py'],
        check=True,stdout=report,stderr=errors)
assert json.loads((R/'audit-report.json').read_text())['transaction_read_only']
print('read_only_audit_complete',flush=True)
