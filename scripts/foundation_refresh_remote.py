"""Fresh feed import and first-session smoke in an isolated copy only."""
from pathlib import Path
import json
import os
import shutil
import subprocess

R=Path('/opt/modish-releases/20260922-foundation')
TAG='modish-backend:foundation-20260922'
NETWORK='modish-foundation-test'
def inspect(name):return json.loads(subprocess.check_output(['docker','inspect',name]))[0]
os.umask(0o077)
BASE='sha256:4ae234fc8a1f8082bb368f4f617d187a6138bb5848c02af5e9a7a9f15a3d6d7e'
assert inspect('modish_backend')['Image']==BASE
assert set(inspect('modish_foundation_test_pg')['NetworkSettings']['Networks'])=={NETWORK}
assert shutil.disk_usage(R).free>2_000_000_000
assert json.loads((R/'freshness-report.json').read_text())['result']=='passed'
# Preserve the previous experiment before replacing its snapshots and reports.
history=R/'before-refresh-20261006'
history.mkdir(mode=0o700,exist_ok=True)
for name in ('download-report.json','refresh-report.json','profiles-report.json'):
    source=R/name
    if source.exists() and not (history/name).exists(): shutil.copy2(source,history/name)
(R/'feeds').mkdir(mode=0o700,exist_ok=True)
with (R/'download-report.json').open('w') as out,(R/'download.log').open('w') as err:
    subprocess.run(['docker','run','--rm','--network','modish_default','--env-file','/opt/Modish/backend/.env',
        '-v',str(R/'feeds')+':/feeds','-v',str(R/'refresh-feeds.py')+':/download.py:ro',
        '-e','PYTHONPATH=/app','--entrypoint','python',TAG,'/download.py'],check=True,stdout=out,stderr=err)
print('fresh_feeds_downloaded',flush=True)
with (R/'refresh.log').open('w') as log:
    subprocess.run(['docker','run','--rm','--network',NETWORK,'--env-file',str(R/'test-api.env'),
        '-v',str(R/'feeds')+':/feeds:ro','-v',str(R/'refresh-clone.py')+':/refresh.py:ro',
        '--entrypoint','python',TAG,'/refresh.py'],check=True,stdout=log,stderr=subprocess.STDOUT)
report=[json.loads(line) for line in (R/'refresh.log').read_text().splitlines() if line.startswith('{')][-1]
(R/'refresh-report.json').write_text(json.dumps(report,indent=2))
assert report['result']=='passed'
print('fresh_clone_import_passed',flush=True)
subprocess.run(['python3','-u',str(R/'profiles-runner.py')],check=True)
assert inspect('modish_backend')['Image']==BASE
