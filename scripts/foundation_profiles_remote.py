"""Rerun profiles only against the already refreshed isolated clone."""
from pathlib import Path
from datetime import datetime, timezone
import json
import os
import subprocess

os.umask(0o077)
r=Path('/opt/modish-releases/20260922-foundation')
BASE='sha256:4ae234fc8a1f8082bb368f4f617d187a6138bb5848c02af5e9a7a9f15a3d6d7e'
assert json.loads(subprocess.check_output(['docker','inspect','modish_backend']))[0]['Image']==BASE
assert json.loads((r/'refresh-report.json').read_text())['result']=='passed'
with (r/'profiles.log').open('w') as log:
    subprocess.run(['docker','run','--rm','--network','modish-foundation-test',
        '--env-file',str(r/'test-api.env'),'-v',str(r/'profiles.py')+':/profiles.py:ro',
        '--entrypoint','python','modish-backend:foundation-20260922','/profiles.py'],
        check=True,stdout=log,stderr=subprocess.STDOUT)
report=[json.loads(line) for line in (r/'profiles.log').read_text().splitlines() if line.startswith('{')][-1]
report['image_id']=json.loads(subprocess.check_output(['docker','image','inspect','modish-backend:foundation-20260922']))[0]['Id']
report['production_unchanged']=json.loads(subprocess.check_output(['docker','inspect','modish_backend']))[0]['Image']==BASE
report['checked_at']=datetime.now(timezone.utc).isoformat()
assert report['production_unchanged']
for profile in report.get('profiles',[]): profile.pop('preview',None)
(r/'profiles-report.json').write_text(json.dumps(report,indent=2))
print('profiles_complete: '+report['result'],flush=True)
