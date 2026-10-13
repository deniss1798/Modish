"""Fixed isolated-stage operations. SSH password is read without terminal echo."""
import getpass
import hashlib
import json
import os
import re
from pathlib import Path
import shlex
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'.tmp/ssh-runtime'))
import paramiko
from release9_ssh_connection import ready_socket

REMOTE = '/opt/modish-releases/20261008-intent-v14'
ARCHIVE = 'modish-foundation-v14.tar'
RELEASE='v14'
if '--v16' in sys.argv:
    REMOTE='/opt/modish-releases/20261008-intent-v16'
    ARCHIVE='modish-foundation-v16.tar'
    RELEASE='v16'
if '--v17' in sys.argv:
    REMOTE='/opt/modish-releases/20261008-intent-v17'
    ARCHIVE='modish-foundation-v17.tar'
    RELEASE='v17'
if '--v18' in sys.argv:
    REMOTE='/opt/modish-releases/20261009-intent-v18'
    ARCHIVE='modish-foundation-v18.tar'
    RELEASE='v18'
if '--v19' in sys.argv:
    REMOTE='/opt/modish-releases/20261009-intent-v19'
    ARCHIVE='modish-foundation-v19.tar'
    RELEASE='v19'
if '--v20' in sys.argv:
    REMOTE='/opt/modish-releases/20261009-intent-v20'
    ARCHIVE='modish-foundation-v20.tar'
    RELEASE='v20'
if '--v21' in sys.argv:
    REMOTE='/opt/modish-releases/20261010-intent-v21'
    ARCHIVE='modish-foundation-v21.tar'
    RELEASE='v21'
if '--v22' in sys.argv:
    REMOTE='/opt/modish-releases/20261010-intent-v22'
    ARCHIVE='modish-foundation-v22.tar'
    RELEASE='v22'
if '--v24' in sys.argv:
    REMOTE='/opt/modish-releases/20261012-intent-v24'
    ARCHIVE='modish-foundation-v24.tar'
    RELEASE='v24'
if '--v26' in sys.argv:
    REMOTE='/opt/modish-releases/20261013-intent-v26'
    ARCHIVE='modish-foundation-v26.tar'
    RELEASE='v26'
if '--v28' in sys.argv:
    REMOTE='/opt/modish-releases/20261013-intent-v28'
    ARCHIVE='modish-foundation-v28.tar'
    RELEASE='v28'
operation = sys.argv[1]
assert operation in {'status', 'start', 'poll', 'fetch', 'watch', 'profile_read', 'resume_offline', 'stop_old_audit', 'warm_smoke', 'watch_warm', 'metrics_smoke','metrics_retry','metrics_retry2','metrics_log'}
client = paramiko.SSHClient()
client.load_host_keys(os.path.expanduser('~/.ssh/known_hosts'))
if '--password-port' in sys.argv:
    listener=socket.socket()
    listener.bind(('127.0.0.1',0));listener.listen(1);listener.settimeout(120)
    print(json.dumps({'password_port':listener.getsockname()[1]}),flush=True)
    connection,_=listener.accept();listener.close()
    with connection, connection.makefile('r',encoding='utf-8') as incoming:
        password=json.loads(incoming.readline())['password']
else:
    password = getpass.getpass('SSH password (not echoed): ')
try:
    client.connect('194.87.118.236', username='root', password=password, sock=ready_socket(),
        look_for_keys=False, allow_agent=False, timeout=20, banner_timeout=20, auth_timeout=15)
    if operation == 'status':
        code = """import json,subprocess,shutil
from datetime import datetime,timezone
def inspect(name): return json.loads(subprocess.check_output(['docker','inspect',name]))[0]
report={'checked_at':datetime.now(timezone.utc).isoformat(),'free_bytes':shutil.disk_usage('/opt').free}
for name in ('modish_backend','modish_postgres','modish_redis','modish_foundation_test_pg'):
 c=inspect(name);report[name]={'id':c['Id'],'image':c['Image'],'running':c['State']['Running']}
network=json.loads(subprocess.check_output(['docker','network','inspect','modish-foundation-test']))[0]
report['network_internal']=network['Internal']
query='SELECT version_num FROM alembic_version; SELECT count(*) FROM products; SELECT source, count(*), max(last_seen_in_feed_at) FROM products GROUP BY source ORDER BY source'
report['test_catalog']=subprocess.check_output(['docker','exec','modish_foundation_test_pg','psql','-U','modish_test','-d','modish_test','-Atc',query],text=True).splitlines()
print(json.dumps(report))"""
        command = 'python3 -c '+shlex.quote(code)
    elif operation == 'start':
        archive = ROOT/'.tmp'/ARCHIVE
        sha = hashlib.sha256(archive.read_bytes()).hexdigest()
        checked=json.loads((ROOT/'docs/modish2'/('foundation-'+RELEASE+'-package-check.json')).read_text(encoding='utf-8'))
        assert checked['test_exit_code']==0 and checked['archive_sha256']==sha
        files=[(archive,'source.tar'),(ROOT/'scripts'/('intent_v19_remote.py' if RELEASE=='v19' else 'intent_v18_remote.py' if RELEASE=='v18' else 'intent_v17_remote.py' if RELEASE=='v17' else 'intent_v16_remote.py' if RELEASE=='v16' else 'intent_stage_remote.py'),'stage.py'),
            (ROOT/'scripts'/('intent_v17_performance.py' if RELEASE in {'v17','v18'} else 'intent_catalog_audit.py'),'performance.py' if RELEASE in {'v17','v18'} else 'catalog-audit.py')]
        if RELEASE in {'v20','v21','v22','v24','v26','v28'}:
            files=[(archive,'source.tar'),(ROOT/'scripts'/('intent_'+RELEASE+'_remote.py'),'stage.py'),
                (ROOT/'scripts'/('intent_v23_activation_performance.py' if RELEASE in {'v24','v26','v28'} else 'intent_v20_activation_performance.py'),'activation-performance.py'),
                (ROOT/'scripts/intent_v19_pg_metrics.py','metrics-smoke.py'),
                (ROOT/'scripts/audit_merchant_size_fields_v20.py','size-raw-inventory.py')]
        if RELEASE in {'v18','v19'}:
            files.append((ROOT/'scripts'/('intent_v19_pg_metrics.py' if RELEASE=='v19' else 'intent_v17_pg_metrics.py'),'metrics-smoke.py'))
        with client.open_sftp() as sftp:
            try: sftp.mkdir(REMOTE, mode=0o700)
            except IOError: sftp.stat(REMOTE)
            # Never replace an existing uploaded release.
            for source, name in files:
                target=REMOTE+'/'+name
                try: sftp.stat(target)
                except IOError: pass
                else: raise RuntimeError('Release destination already exists: '+name)
                sftp.put(str(source),target);sftp.chmod(target,0o600)
        stage_args=[sha]
        if RELEASE in {'v20','v21','v22','v24','v26','v28'}:
            stage_args += ['--expected-tests',re.search(r'Ran (\d+) tests',checked['test_summary']).group(1),'--runs','3']
            if '--profile' in sys.argv:
                stage_args.append('--profile')
            for local,flag in [('intent_v19_pg_metrics.py','--metrics-helper-sha256'),
                ('intent_v23_activation_performance.py' if RELEASE in {'v24','v26','v28'} else 'intent_v20_activation_performance.py','--activation-helper-sha256'),
                ('audit_merchant_size_fields_v20.py','--inventory-helper-sha256')]:
                stage_args += [flag,hashlib.sha256((ROOT/'scripts'/local).read_bytes()).hexdigest()]
        code="""import subprocess,json
from pathlib import Path
r=Path('__REMOTE__')
with (r/'runner.log').open('w') as log:
 p=subprocess.Popen(['python3','-u',str(r/'stage.py'),*json.loads('__ARGS__')],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
print('isolated_stage_started',p.pid)""".replace('__REMOTE__',REMOTE).replace('__ARGS__',json.dumps(stage_args))
        command='python3 -c '+shlex.quote(code)
    elif operation == 'profile_read':
        assert RELEASE in {'v20','v21','v22','v24','v26','v28'}
        with client.open_sftp() as sftp:
            with sftp.open(REMOTE+'/baseline-activation-performance.json') as source:
                profile=json.loads(source.read().decode())
        (ROOT/'docs/modish2'/('intent-'+RELEASE+'-baseline-activation-performance.json')).write_text(json.dumps(profile,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        row=profile.get('profile_run') or {}
        print(json.dumps({'profile_read':{name:{'seconds':phase['seconds'],'profile':phase.get('profile',phase.get('profiling',{}))} for name,phase in row.get('phases',{}).items() if name in {'cold_deck','first_feed'}}},ensure_ascii=True))
        command='true'
    elif operation == 'metrics_log':
        assert RELEASE=='v19'
        code="from pathlib import Path;r=Path('__REMOTE__');p=r/'metrics-retry-errors.log';print((p if p.exists() else r/'metrics-smoke.log').read_text()[-6000:])".replace('__REMOTE__',REMOTE)
        command='python3 -c '+shlex.quote(code)
    elif operation in {'metrics_retry','metrics_retry2'}:
        assert RELEASE=='v19'
        suffix='2' if operation=='metrics_retry2' else ''
        with client.open_sftp() as sftp:
            for local, remote in [('intent_v19_pg_metrics.py','metrics-smoke-retry'+suffix+'.py'),('intent_v19_remote_retry'+suffix+'.py','metrics-retry'+suffix+'.py')]:
                target=REMOTE+'/'+remote
                try: sftp.stat(target)
                except IOError: pass
                else: raise RuntimeError('Retry file already prepared')
                sftp.put(str(ROOT/'scripts'/local),target);sftp.chmod(target,0o600)
        code="""import subprocess
from pathlib import Path
r=Path('__REMOTE__')
with (r/'runner-retry__SUFFIX__.log').open('w') as log:
 p=subprocess.Popen(['python3','-u',str(r/'metrics-retry__SUFFIX__.py')],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
print('isolated_metrics_retry_started',p.pid)""".replace('__REMOTE__',REMOTE).replace('__SUFFIX__',suffix)
        command='python3 -c '+shlex.quote(code)
    elif operation == 'metrics_smoke':
        assert RELEASE=='v17'
        with client.open_sftp() as sftp:
            target=REMOTE+'/metrics-smoke.py'
            try: sftp.stat(target)
            except IOError: pass
            else: raise RuntimeError('Metrics smoke already prepared')
            sftp.put(str(ROOT/'scripts/intent_v17_pg_metrics.py'),target);sftp.chmod(target,0o600)
        code='''import json,subprocess
from pathlib import Path
r=Path('__REMOTE__')
report=json.loads((r/'stage-report.json').read_text())
image=report['image_id']
assert image=='sha256:a4a0ae7f05f82c082c00fe06284cf95803f8c1d6fbf7a4d9747d109624e3ab24'
assert report['archive_sha256']=='bff0e63e0b1bb929f463622b0ffeb7182f1934b4cbe6996e3da2dedfbbc75d2b'
with (r/'metrics-smoke.json').open('w') as output,(r/'metrics-smoke.log').open('w') as error:
 result=subprocess.run(['docker','run','--rm','--cpus=0.75','--memory=768m','--network','modish-foundation-test',
  '--env-file','/opt/modish-releases/20260922-foundation/test-api.env','-e','AUDIT_IMAGE_ID='+image,
  '-v',str(r/'metrics-smoke.py')+':/smoke.py:ro','--entrypoint','python',image,'/smoke.py'],stdout=output,stderr=error,timeout=120)
assert result.returncode==0, 'Isolated metric fixture failed; inspect scoped log'
assert json.loads(subprocess.check_output(['docker','inspect','modish_backend']))[0]['Image']=='sha256:4ae234fc8a1f8082bb368f4f617d187a6138bb5848c02af5e9a7a9f15a3d6d7e'
print(json.dumps({'metrics_smoke':json.loads((r/'metrics-smoke.json').read_text()),'production_unchanged':True}))'''.replace('__REMOTE__',REMOTE)
        command='python3 -c '+shlex.quote(code)
    elif operation == 'warm_smoke':
        assert RELEASE=='v16'
        with client.open_sftp() as sftp:
            target=REMOTE+'/warm-smoke.py'
            try: sftp.stat(target)
            except IOError: pass
            else: raise RuntimeError('Warm smoke already prepared')
            sftp.put(str(ROOT/'scripts/intent_warm_smoke.py'),target);sftp.chmod(target,0o600)
        code="""import json,subprocess
from pathlib import Path
r=Path('__REMOTE__')
report=json.loads((r/'stage-report.json').read_text())
assert report['archive_sha256']=='6726baf5e159a4407628fba0a84cc35d42b2c97294fae1afa355dcd053828d20'
image=report['image_id']
assert image=='sha256:c3a1e0501448a592e303750fb0ba9341cb6e7ba841c4123a434c033d0a2ff55e'
with (r/'warm-smoke.json').open('w') as output,(r/'warm-smoke.log').open('w') as error:
 p=subprocess.Popen(['docker','run','--rm','--cpus=0.75','--memory=768m','--network','modish-foundation-test',
  '--env-file','/opt/modish-releases/20260922-foundation/test-api.env','-e','AUDIT_IMAGE_ID='+image,
  '-e','AUDIT_ARCHIVE_SHA256='+report['archive_sha256'],'-v',str(r/'warm-smoke.py')+':/smoke.py:ro',
  '--entrypoint','python',image,'/smoke.py'],stdin=subprocess.DEVNULL,stdout=output,stderr=error,start_new_session=True)
print('isolated_warm_smoke_started',p.pid)""".replace('__REMOTE__',REMOTE)
        command='python3 -c '+shlex.quote(code)
    elif operation == 'stop_old_audit':
        code="""import json,subprocess
from pathlib import Path
from urllib.parse import urlsplit
r=Path('__REMOTE__')
assert r==Path('/opt/modish-releases/20261008-intent-v14')
image=json.loads(subprocess.check_output(['docker','image','inspect','modish-backend:intent-v14-20261008']))[0]['Id']
stopped=[]
for cid in subprocess.check_output(['docker','ps','--filter','ancestor='+image,'-q'],text=True).splitlines():
 c=json.loads(subprocess.check_output(['docker','inspect',cid]))[0]
 assert c['Config']['Cmd']==['/audit.py']
 assert set(c['NetworkSettings']['Networks'])=={'modish-foundation-test'}
 assert any(m['Source']==str(r/'catalog-audit.py') and m['Destination']=='/audit.py' and not m['RW'] for m in c['Mounts'])
 env=dict(v.split('=',1) for v in c['Config']['Env'] if '=' in v)
 assert urlsplit(env['DATABASE_URL'].replace('postgresql+psycopg://','postgresql://')).hostname=='modish_foundation_test_pg'
 subprocess.run(['docker','stop','-t','10',cid],check=True,stdout=subprocess.DEVNULL)
 stopped.append(cid)
report={'scope':'isolated_clone_audit_only','stopped_containers':stopped,'reason':'Restart with admission before expensive semantic parsing','production_changed':False}
(r/'audit-stopped.json').write_text(json.dumps(report))
print(json.dumps(report))""".replace('__REMOTE__',REMOTE)
        command='python3 -c '+shlex.quote(code)
    elif operation == 'resume_offline':
        with client.open_sftp() as sftp:
            target=REMOTE+'/stage-offline.py'
            try: sftp.stat(target)
            except IOError: pass
            else: raise RuntimeError('Offline runner already uploaded')
            sftp.put(str(ROOT/'scripts/intent_stage_offline_remote.py'),target);sftp.chmod(target,0o600)
        sha=hashlib.sha256((ROOT/'.tmp'/ARCHIVE).read_bytes()).hexdigest()
        code="""import subprocess
from pathlib import Path
r=Path('__REMOTE__')
assert subprocess.run(['pgrep','-f','python3 -u '+str(r/'stage.py')],stdout=subprocess.DEVNULL).returncode!=0
with (r/'runner-offline.log').open('w') as log:
 p=subprocess.Popen(['python3','-u',str(r/'stage-offline.py'),'__SHA__'],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
print('isolated_offline_stage_started',p.pid)""".replace('__REMOTE__',REMOTE).replace('__SHA__',sha)
        command='python3 -c '+shlex.quote(code)
    elif operation == 'watch_warm':
        assert RELEASE=='v16'
        code=r"""import json,time,re,subprocess
from pathlib import Path
r=Path('__REMOTE__')
previous=None
for attempt in range(120):
 p=r/'warm-smoke.log'
 lines=p.read_text(errors='replace').splitlines() if p.exists() else []
 progress=[line for line in lines if line.startswith('catalog_warm_profile_completed')][-5:]
 if progress!=previous: print(json.dumps({'progress':progress}),flush=True);previous=progress
 errors=[line.split(':',1)[0] for line in lines if re.match(r'^(?:[A-Za-z_]+[.])*[A-Za-z]+(?:Error|Exception):',line)]
 if errors: print(json.dumps({'error_types':errors[-2:]}),flush=True);break
 result=r/'warm-smoke.json'
 if result.exists() and result.stat().st_size:
  try: report=json.loads(result.read_text())
  except ValueError: pass
  else:
   assert json.loads(subprocess.check_output(['docker','inspect','modish_backend']))[0]['Image']=='sha256:4ae234fc8a1f8082bb368f4f617d187a6138bb5848c02af5e9a7a9f15a3d6d7e'
   print(json.dumps({'warm_smoke_result':report,'production_image_unchanged':True}),flush=True);break
 time.sleep(15)
""".replace('__REMOTE__',REMOTE)
        command='python3 -u -c '+shlex.quote(code)
    elif operation == 'watch':
        code="""import json,time,re
from pathlib import Path
r=Path('__REMOTE__')
previous=None
for attempt in range(120):
 progress=[]
 for name in (('runner-retry2.log','metrics-retry2-errors.log') if (r/'runner-retry2.log').exists() else ('runner-retry.log','metrics-retry-errors.log') if (r/'runner-retry.log').exists() else ('runner.log','runner-offline.log','unit-tests.log','migration-test.log','catalog-errors.log','baseline-errors.log','current-errors.log','baseline-activation-errors.log','current-activation-errors.log','metrics-smoke.log')):
  p=r/name
  if not p.exists(): continue
  lines=p.read_text(errors='replace').splitlines()
  safe=[s[:250] for s in lines if s.startswith(('stage_','image_','unit_','migration_','catalog_','performance_','latency_','positive_pg_','activation_benchmark_','Ran ','OK','FAILED','AssertionError:','RuntimeError:','INFO  [alembic'))]
  errors=[s.split(':',1)[0] for s in lines if re.match(r'^(?:[A-Za-z_]+[.])*[A-Za-z]+(?:Error|Exception):',s)]
  progress.append({'file':name,'progress':safe[-5:],'error_types':errors[-2:]})
 encoded=json.dumps(progress)
 if encoded!=previous: print(encoded,flush=True);previous=encoded
 failure=r/'stage-failure.json'
 if failure.exists(): print(json.dumps({'stage_failure':json.loads(failure.read_text())}),flush=True);break
 p=r/'stage-report.json'
 if p.exists(): print(json.dumps({'stage_report':json.loads(p.read_text())}),flush=True);break
 if any(row['error_types'] for row in progress if row['file']!='unit-tests.log'): break
 time.sleep(15)
""".replace('__REMOTE__',REMOTE)
        command='python3 -u -c '+shlex.quote(code)
    elif operation == 'poll':
        code=r"""import json,re,subprocess
from pathlib import Path
r=Path('__REMOTE__')
for name in ('stage-report.json','catalog-report.json'):
 p=r/name
 if p.exists(): print(json.dumps({'file':name,'report':json.loads(p.read_text())}))
for name in ('runner.log','runner-offline.log','build-offline.log','unit-tests.log','migration-test.log','catalog-errors.log','baseline-errors.log','current-errors.log','metrics-smoke.log'):
 p=r/name
 if not p.exists(): continue
 lines=p.read_text(errors='replace').splitlines()
 safe=[s[:250] for s in lines if s.startswith(('stage_','image_','unit_','migration_','catalog_','performance_','latency_','positive_pg_','Ran ','OK','FAILED','ERROR:','AssertionError:','RuntimeError:','INFO  [alembic'))]
 errors=[s.split(':',1)[0] for s in lines if re.match(r'^(?:[A-Za-z_]+[.])*[A-Za-z]+(?:Error|Exception):',s)]
 print(json.dumps({'file':name,'progress':safe[-8:],'error_types':errors[-3:]}))
p=r/'build-offline.log'
if p.exists():
 steps=[line[:180] for line in p.read_text(errors='replace').splitlines() if re.match(r'^#\d+ (?:\[|DONE|CACHED|ERROR)',line)]
 print(json.dumps({'build_log_bytes':p.stat().st_size,'build_steps':steps[-8:]}))
print(json.dumps({'stage_running':subprocess.run(['pgrep','-f','python3 -u '+str(r/'stage-offline.py')],stdout=subprocess.DEVNULL).returncode==0}))""".replace('__REMOTE__',REMOTE)
        command='python3 -c '+shlex.quote(code)
    else:
        with client.open_sftp() as sftp:
            for name in ('stage-report.json','catalog-report.json'):
                sftp.get(REMOTE+'/'+name,str(ROOT/'docs/modish2'/('intent-'+RELEASE+'-'+name)))
        command='true'
    _, out, err = client.exec_command(command,timeout=1800 if operation in {'watch','watch_warm'} else 150 if operation=='metrics_smoke' else 45)
    if operation in {'watch','watch_warm'}:
        for line in out:
            print(line.rstrip(),flush=True)
        output='Watch finished'
        with client.open_sftp() as sftp:
            for name in (('warm-smoke.json',) if operation=='watch_warm' else ('stage-report.json','catalog-report.json','baseline-activation-performance.json','current-activation-performance.json','metrics-smoke.json','size-raw-inventory.json','stage-failure.json') if RELEASE in {'v20','v21','v22','v24','v26','v28'} else ('stage-report.json','catalog-report.json','metrics-retry2-report.json') if RELEASE=='v19' else ('stage-report.json','catalog-report.json','metrics-smoke.json') if RELEASE=='v18' else ('stage-report.json','catalog-report.json')):
                try:
                    with sftp.open(REMOTE+'/'+name) as source: report=json.loads(source.read().decode())
                    (ROOT/'docs/modish2'/('intent-'+RELEASE+'-'+name)).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
                except (IOError,ValueError): pass
    else:
        output=out.read().decode()
    if operation=='metrics_smoke':
        with client.open_sftp() as sftp:
            with sftp.open(REMOTE+'/metrics-smoke.json') as source: metrics=json.loads(source.read().decode())
            (ROOT/'docs/modish2/intent-v17-pg-metrics-smoke.json').write_text(json.dumps(metrics,indent=2)+'\n',encoding='utf-8')
    errors=err.read().decode()
    status=out.channel.recv_exit_status()
    print(json.dumps({'exit_code':status,'stdout':output,'stderr':errors}).replace(password,'[REDACTED]'))
except Exception as exc:
    print(json.dumps({'operation':operation,'error_type':type(exc).__name__,'completed':False}))
    raise SystemExit(1)
finally:
    password=None
    client.close()
