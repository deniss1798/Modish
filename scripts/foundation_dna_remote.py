"""Build the reviewed Foundation package and audit only the isolated catalogue."""
from pathlib import Path
import hashlib
import json
import subprocess

R = Path('/opt/modish-releases/20260922-foundation')
BASE = 'sha256:4ae234fc8a1f8082bb368f4f617d187a6138bb5848c02af5e9a7a9f15a3d6d7e'
TAG = 'modish-backend:foundation-20260922'


def inspect(name):
    return json.loads(subprocess.check_output(['docker', 'inspect', name]))[0]


assert inspect('modish_backend')['Image'] == BASE
subprocess.run(['python3', '-u', str(R/'audit-runner.py'), '--build-only'], check=True)
with (R/'dna-audit-report.json').open('w') as out, (R/'dna-audit-errors.log').open('w') as err:
    subprocess.run(['docker', 'run', '--rm', '--network', 'modish-foundation-test',
        '--env-file', str(R/'test-api.env'), '-v', str(R/'dna-audit.py')+':/audit.py:ro',
        '--entrypoint', 'python', TAG, '/audit.py'], check=True, stdout=out, stderr=err)
report = json.loads((R/'dna-audit-report.json').read_text())
assert report['scope'] == 'isolated_clone_read_only'
assert inspect('modish_backend')['Image'] == BASE
report.update(image_id=inspect(TAG)['Id'], production_unchanged=True,
    archive_sha256=hashlib.sha256((R/'source.tar').read_bytes()).hexdigest())
(R/'dna-audit-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
print('dna_audit_completed', flush=True)
