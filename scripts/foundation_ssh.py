"""Fixed Catalog Foundation operations; password travels through memory only."""
import json
import os
from pathlib import Path
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'.tmp/ssh-runtime'))
import paramiko
from release9_ssh_connection import ready_socket

R = '/opt/modish-releases/20260922-foundation'
operation = sys.argv[1]
assert operation in {'audit', 'status', 'fetch', 'stage', 'audit_retry', 'probe', 'feed_destinations', 'verify', 'refresh_clone', 'refresh_status', 'swipe_smoke', 'personalization_smoke', 'profiles', 'build_profiles', 'size_audit', 'dna_audit', 'dna_status', 'dna_review'}
listener = socket.socket()
listener.bind(('127.0.0.1', 0))
listener.listen(1)
listener.settimeout(120)
print(listener.getsockname()[1], flush=True)
sock, _ = listener.accept()
listener.close()
with sock, sock.makefile('r', encoding='utf-8') as incoming:
    secret = json.loads(incoming.readline())['password']
    client = paramiko.SSHClient()
    client.load_host_keys(os.path.expanduser('~/.ssh/known_hosts'))
    try:
        client.connect('194.87.118.236', username='root', password=secret, sock=ready_socket(),
                       look_for_keys=False, allow_agent=False, timeout=20, banner_timeout=20, auth_timeout=15)
        if operation == 'dna_review':
            with client.open_sftp() as sftp:
                sftp.put(str(ROOT/'scripts/foundation_dna_review_sample.py'), R+'/dna-review.py')
                sftp.chmod(R+'/dna-review.py', 0o600)
            # Pin the already verified image, not a mutable tag.
            command = "docker run --rm --network modish-foundation-test --env-file "+R+"/test-api.env -v "+R+"/dna-review.py:/audit.py:ro --entrypoint python sha256:f57fbfe8d458ada771ea0a0658070881ffe409e3b3d05c3fcb7ea2de7f70e293 /audit.py"
        elif operation == 'personalization_smoke':
            with client.open_sftp() as sftp:
                sftp.put(str(ROOT/'scripts/foundation_personalization_smoke.py'), R+'/personalization-smoke.py')
                sftp.chmod(R+'/personalization-smoke.py', 0o600)
            command = """python3 - <<'PY'
import hashlib,json,subprocess
from pathlib import Path
r=Path('__REMOTE__')
assert json.loads((r/'refresh-report.json').read_text())['result']=='passed'
audit=json.loads((r/'dna-audit-report.json').read_text())
assert audit['archive_sha256']==hashlib.sha256((r/'source.tar').read_bytes()).hexdigest()
image=audit['image_id']
assert subprocess.check_output(['docker','image','inspect','modish-backend:foundation-20260922','--format','{{.Id}}'],text=True).strip()==image
with (r/'personalization-smoke.log').open('w') as log:
 p=subprocess.Popen(['docker','run','--rm','--network','modish-foundation-test','--env-file',str(r/'test-api.env'),'-e','MODISH_SMOKE_IMAGE_ID='+image,'-e','MODISH_SMOKE_ARCHIVE_SHA256='+audit['archive_sha256'],'-v',str(r/'personalization-smoke.py')+':/smoke.py:ro','--entrypoint','python',image,'/smoke.py'],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
print('Personalization smoke started:',p.pid)
PY""".replace('__REMOTE__',R)
        elif operation == 'swipe_smoke':
            with client.open_sftp() as sftp:
                sftp.put(str(ROOT/'scripts/foundation_swipe_smoke.py'), R+'/swipe-smoke.py')
                sftp.chmod(R+'/swipe-smoke.py', 0o600)
            command = "python3 - <<'PY'\nimport json,subprocess\nfrom pathlib import Path\nr=Path('"+R+"')\nassert json.loads((r/'refresh-report.json').read_text())['result']=='passed'\nwith (r/'swipe-smoke.log').open('w') as log:\n p=subprocess.Popen(['docker','run','--rm','--network','modish-foundation-test','--env-file',str(r/'test-api.env'),'-v',str(r/'swipe-smoke.py')+':/smoke.py:ro','--entrypoint','python','modish-backend:foundation-20260922','/smoke.py'],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)\nprint('Swipe smoke started:',p.pid)\nPY"
        elif operation == 'size_audit':
            with client.open_sftp() as sftp:
                sftp.put(str(ROOT/'scripts/foundation_size_audit.py'),R+'/size-audit.py')
                sftp.chmod(R+'/size-audit.py',0o600)
            command = "docker run --rm --network modish-foundation-test --env-file "+R+"/test-api.env -v "+R+"/size-audit.py:/audit.py:ro --entrypoint python modish-backend:foundation-20260922 /audit.py"
        elif operation == 'refresh_status':
            command = """python3 - <<'PY'
import json,re
from pathlib import Path
r=Path('__REMOTE__')
for name in ('refresh_clone-runner.log','download.log','refresh.log','profiles.log','swipe-smoke.log','personalization-smoke.log'):
 p=r/name
 if not p.exists(): continue
 if name=='profiles.log' and 'fresh_clone_import_passed' not in (r/'refresh_clone-runner.log').read_text():
  print(json.dumps({'file':name,'state':'not_started_this_run'}));continue
 lines=p.read_text().splitlines();progress=[];final=None
 for line in lines:
  if line.startswith('{'):
   try: d=json.loads(line)
   except ValueError: continue
   if 'progress' in d or 'downloaded' in d: progress.append(d)
   elif 'result' in d: final={k:d[k] for k in ('result','unique_cards','target_reached','duplicate_models','final_refill','wow_coverage','checks','search','intent_did_not_modify_taste') if k in d}
  elif line.startswith(('fresh_','profiles_complete:')): progress.append(line)
 types=[line.split(':',1)[0] for line in lines if re.match(r'^[A-Za-z]+(?:Error|Exception):',line)]
 assertions=[line[:240] for line in lines if line.startswith('AssertionError:')] if name in {'swipe-smoke.log','personalization-smoke.log'} else []
 print(json.dumps({'file':name,'progress':progress[-6:],'final':final,'exception_types':types[-1:],'assertions':assertions[-1:]}))
PY""".replace('__REMOTE__',R)
        elif operation == 'dna_status':
            command = "python3 - <<'PY'\nimport json\nfrom pathlib import Path\nr=Path('"+R+"')\nfor name in ('dna_audit-runner.log','dna-audit-errors.log','unit-tests.log'):\n p=r/name\n if p.exists(): print(json.dumps({'file':name,'tail':p.read_text()[-1800:]}))\nPY"
        elif operation == 'feed_destinations':
            code = "from urllib.parse import urlsplit,parse_qs;import json;from sqlalchemy import text;from app.db import SessionLocal;db=SessionLocal();db.execute(text('SET TRANSACTION READ ONLY'));rows=db.execute(text('SELECT code,feed_url FROM product_sources ORDER BY code'));print(json.dumps([{'code':c,'host':urlsplit(u or '').hostname,'query_keys':list(parse_qs(urlsplit(u or '').query)),'userinfo_present':bool(urlsplit(u or '').username)} for c,u in rows]));db.rollback();db.close()"
            import shlex
            command = 'docker exec modish_backend python -c ' + shlex.quote(code)
        elif operation == 'probe':
            with client.open_sftp() as sftp:
                sftp.put(str(ROOT/'scripts/foundation_feed_probe.py'),R+'/feed-probe.py')
                sftp.chmod(R+'/feed-probe.py',0o600)
            command = "python3 - <<'PY'\nimport subprocess\nfrom pathlib import Path\nr=Path('"+R+"')\nwith (r/'feed-probe-report.json').open('w') as out, (r/'feed-probe.log').open('w') as err:\n p=subprocess.Popen(['docker','run','--rm','--network','modish_default','--env-file','/opt/Modish/backend/.env','-v',str(r/'feed-probe.py')+':/probe.py:ro','-e','PYTHONPATH=/app','--entrypoint','python','modish-backend:build11-20260922','/probe.py'],stdin=subprocess.DEVNULL,stdout=out,stderr=err,start_new_session=True)\nprint('Feed probe started:',p.pid)\nPY"
        elif operation in {'audit', 'stage', 'audit_retry', 'verify', 'refresh_clone', 'profiles', 'build_profiles', 'dna_audit'}:
            files = [('.tmp/modish-foundation.tar', 'source.tar'),
                     ('scripts/foundation_remote.py', 'audit-runner.py'),
                     ('scripts/audit_foundation_live.py', 'audit.py')] if operation == 'audit' else [
                     ('scripts/foundation_stage.py', 'stage.py'), ('scripts/smoke_foundation.py', 'smoke.py'),
                     ('scripts/smoke_release9.py', 'smoke_release9.py')]
            if operation == 'audit_retry':
                files = [('scripts/audit_foundation_live.py', 'audit.py'),
                         ('scripts/foundation_audit_retry.py', 'audit-retry.py')]
            if operation == 'verify':
                files = [('.tmp/modish-foundation.tar','source.tar'),
                         ('scripts/foundation_remote.py','audit-runner.py'),
                         ('scripts/foundation_verify.py','verify.py')]
            if operation == 'refresh_clone':
                files = [('scripts/foundation_refresh_remote.py','refresh-remote.py'),
                         ('scripts/foundation_refresh_feeds.py','refresh-feeds.py'),
                         ('scripts/foundation_refresh_clone.py','refresh-clone.py'),
                         ('scripts/foundation_profiles_remote.py','profiles-runner.py'),
                         ('.tmp/foundation-profiles.py','profiles.py')]
            if operation == 'profiles':
                files = [('scripts/foundation_profiles_remote.py','profiles-runner.py'),
                         ('.tmp/foundation-profiles.py','profiles.py')]
            if operation == 'build_profiles':
                files = [('.tmp/modish-foundation.tar','source.tar'),
                         ('scripts/foundation_remote.py','audit-runner.py'),
                         ('scripts/foundation_profiles_remote.py','profiles-runner.py'),
                         ('scripts/foundation_build_profiles.py','build-profiles.py'),
                         ('.tmp/foundation-profiles.py','profiles.py')]
            if operation == 'dna_audit':
                files = [('.tmp/modish-foundation.tar','source.tar'),
                         ('scripts/foundation_remote.py','audit-runner.py'),
                         ('scripts/foundation_dna_audit.py','dna-audit.py'),
                         ('scripts/foundation_dna_remote.py','dna-remote.py')]
            with client.open_sftp() as sftp:
                try: sftp.mkdir(R, mode=0o700)
                except IOError: sftp.stat(R)
                for source, name in files:
                    sftp.put(str(ROOT/source), R+'/'+name)
                    sftp.chmod(R+'/'+name, 0o600)
            runner = 'audit-runner.py' if operation == 'audit' else 'audit-retry.py' if operation == 'audit_retry' else 'stage.py'
            if operation == 'verify': runner = 'verify.py'
            if operation == 'refresh_clone': runner = 'refresh-remote.py'
            if operation == 'profiles': runner = 'profiles-runner.py'
            if operation == 'build_profiles': runner = 'build-profiles.py'
            if operation == 'dna_audit': runner = 'dna-remote.py'
            command = "python3 - <<'PY'\nimport subprocess\nfrom pathlib import Path\nr=Path('"+R+"')\nwith (r/'"+operation+"-runner.log').open('w') as log:\n p=subprocess.Popen(['python3','-u',str(r/'"+runner+"')],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)\nprint('Started:',p.pid)\nPY"
        elif operation == 'status':
            command = "python3 - <<'PY'\nimport json,subprocess\nfrom pathlib import Path\nr=Path('"+R+"')\nfor name in ('audit-runner.log','unit-tests.log','audit-errors.log','stage-runner.log','migration-test.log','smoke.log','verify-runner.log','freshness-verify.log','feed-probe.log','refresh_clone-runner.log','download.log','refresh.log','profiles.log'):\n p=r/name\n if p.exists():\n  content=p.read_text()\n  if name=='profiles.log': content='\\n'.join(line for line in content.splitlines() if line.startswith('{') and json.loads(line).get('progress'))\n  print(json.dumps({'file':name,'tail':content[-30000:] if name=='profiles.log' else content[-2500:]}))\nprint(subprocess.check_output(['docker','ps','--filter','ancestor=modish-backend:foundation-20260922','--format','{{.ID}} {{.Status}}']).decode())\nPY"
        else:
            names = []
            with client.open_sftp() as sftp:
                for name in ('audit-report.json', 'stage-report.json', 'replay-report.json', 'profiles-report.json', 'feed-probe-report.json', 'freshness-report.json','refresh-report.json','download-report.json','dna-audit-report.json'):
                    try:
                        if not sftp.stat(R+'/'+name).st_size:
                            continue
                    except IOError: continue
                    sftp.get(R+'/'+name, str(ROOT/'docs/modish2'/('foundation-'+name)))
                    names.append(name)
                try:
                    with sftp.open(R+'/personalization-smoke.log') as incoming_report:
                        lines = incoming_report.read().decode().splitlines()
                    report = next((json.loads(line) for line in reversed(lines)
                        if line.startswith('{') and json.loads(line).get('result') == 'passed'), None)
                    if report:
                        target = ROOT/'docs/modish2/foundation-personalization-smoke.json'
                        target.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
                        names.append('personalization-smoke.log')
                except IOError:
                    pass
            command = 'true'
        _, output, error = client.exec_command(command, timeout=40)
        result = {'stdout': output.read().decode(), 'stderr': error.read().decode(),
                  'code': output.channel.recv_exit_status()}
        if operation == 'size_audit' and result['code'] == 0:
            (ROOT/'docs/modish2/foundation-size-evidence.json').write_text(
                json.dumps(json.loads(result['stdout']),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        if operation == 'dna_review' and result['code'] == 0:
            report = json.loads(result['stdout'])
            (ROOT/'docs/modish2/foundation-dna-review-sample.json').write_text(
                json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
            result['stdout'] = json.dumps({'sources': [{
                'source': source['source'], 'construction_review': len(source['construction_review']),
                'conflict_review': len(source['conflict_review'])} for source in report['sources']]})
        if operation == 'fetch': result['downloaded'] = names
        sock.sendall(json.dumps(result).replace(secret, '[REDACTED]').encode()+b'\n')
    except Exception as exc:
        sock.sendall(json.dumps({'error': type(exc).__name__, 'message': str(exc)[:300]}).replace(secret, '[REDACTED]').encode()+b'\n')
    finally:
        client.close()
