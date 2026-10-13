"""Dispatch only the reviewed four-feed refresh on the isolated v20 clone."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.tmp/ssh-runtime'))
import paramiko
from release9_ssh_connection import ready_socket

R = '/opt/modish-releases/20261009-intent-v20'
renew = '--renew-20261013' in sys.argv
retry = '--retry' in sys.argv or renew
fresh_directory = 'refresh-20261013' if renew else 'refresh-retry-20261010' if retry else 'refresh'
prefix = 'refresh-20261013' if renew else 'refresh-retry' if retry else 'refresh'
operation = sys.argv[1]
assert operation in {'start', 'watch', 'diagnose'} and '--password-port' in sys.argv
listener = socket.socket()
listener.bind(('127.0.0.1', 0)); listener.listen(1); listener.settimeout(120)
print(json.dumps({'password_port': listener.getsockname()[1]}), flush=True)
connection, _ = listener.accept(); listener.close()
with connection, connection.makefile('r', encoding='utf-8') as incoming:
    password = json.loads(incoming.readline())['password']
client = paramiko.SSHClient()
client.load_host_keys(os.path.expanduser('~/.ssh/known_hosts'))
try:
    client.connect('194.87.118.236', username='root', password=password, sock=ready_socket(),
        look_for_keys=False, allow_agent=False, timeout=20, banner_timeout=20, auth_timeout=15)
    if operation == 'start':
        report = json.loads((ROOT / 'docs/modish2/intent-v20-stage-report.json').read_text())
        assert report['production_unchanged'] and report['unit_tests']['count'] == 302
        assert report['result'] == 'validation_complete_catalogue_readiness_unproven'
        sha = hashlib.sha256((ROOT / '.tmp/modish-foundation-v20.tar').read_bytes()).hexdigest()
        assert sha == report['archive_sha256']
        uploads = [('intent_v20_refresh_20261013_remote.py' if renew else 'intent_v20_refresh_retry_remote.py' if retry else 'intent_v20_refresh_remote.py', prefix+'-runner.py'),
            ('intent_v20_refresh_download.py', prefix+'-download.py'),
            ('intent_v20_refresh_retry_import.py' if retry else 'intent_v20_refresh_import.py', prefix+'-import.py'),
            ('intent_v20_refresh_readiness.py', prefix+'-readiness.py')]
        with client.open_sftp() as sftp:
            for local, remote in uploads:
                target = R + '/' + remote
                try: sftp.stat(target)
                except IOError: pass
                else: raise RuntimeError('Immutable refresh helper already exists')
                sftp.put(str(ROOT / 'scripts' / local), target); sftp.chmod(target, 0o600)
            with sftp.open(R + '/size-raw-inventory.py') as stream:
                assert hashlib.sha256(stream.read()).hexdigest() == report['helpers']['inventory']['sha256']
        args = [sha, report['image_id']]
        for label, local in [('download', 'intent_v20_refresh_download.py'),
            ('import', 'intent_v20_refresh_retry_import.py' if retry else 'intent_v20_refresh_import.py'), ('readiness', 'intent_v20_refresh_readiness.py'),
            ('inventory', 'audit_merchant_size_fields_v20.py')]:
            args += ['--' + label + '-sha256', hashlib.sha256((ROOT / 'scripts' / local).read_bytes()).hexdigest()]
        code = """import subprocess,json
from pathlib import Path
r=Path('__R__')
with (r/'__PREFIX__-runner.log').open('x') as log:
 p=subprocess.Popen(['python3','-u',str(r/'__PREFIX__-runner.py'),*json.loads('__ARGS__')],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
print('isolated_fresh_refresh_started',p.pid)
""".replace('__R__', R).replace('__ARGS__', json.dumps(args)).replace('__PREFIX__',prefix)
    elif operation == 'diagnose':
        code = """import json,re,subprocess
from pathlib import Path
r=Path('__R__');fresh=r/'__FRESH__';report={}
for name in ('import-report.json','import-errors.log'):
 p=fresh/name;value=p.read_text(errors='replace') if p.exists() else ''
 report[name]={'bytes':len(value.encode()),'error_types':re.findall(r'(?:[A-Za-z_]+[.])*[A-Za-z]+(?:Error|Exception)',value)[-5:]}
 if name.endswith('.json'):
  try:report[name]['safe_report']=json.loads(value)
  except ValueError:pass
 report[name]['docker_oom_message']='out of memory' in value.lower() or 'oom' in value.lower()
report['active_test_helpers']=subprocess.check_output(['docker','ps','--filter','name=modish_v20_refresh_','--format','{{.Names}}'],text=True).splitlines()
failed=json.loads((fresh/('refresh-failure.json' if (fresh/'refresh-failure.json').exists() else 'download-report.json')).read_text())
failed.setdefault('image_id','sha256:cfc4376eed5398c2167527779eb0586eb567dad6d7600251abfdaab32ff69fdc')
events=subprocess.check_output(['docker','events','--since',failed['started_at'],'--until',__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),'--filter','type=container','--filter','image='+failed['image_id'],'--format','{{json .}}'],text=True).splitlines()
report['scoped_container_events']=[{'action':e.get('Action'),'exit_code':e.get('Actor',{}).get('Attributes',{}).get('exitCode')} for line in events for e in [json.loads(line)] if e.get('Actor',{}).get('Attributes',{}).get('name','').startswith('modish_v20_refresh_import_')]
sql="SELECT md5(coalesce(string_agg(md5(row_to_json(p)::text), '' ORDER BY p.id), '')) FROM products p"
report['catalogue_products_match_pre_import']=subprocess.check_output(['docker','exec','modish_foundation_test_pg','psql','-X','-qAt','-U','modish_test','-d','modish_test','-c',sql],text=True).strip()==json.loads((fresh/'before-import.json').read_text())['catalogue']['products_state_digest']
stats=subprocess.check_output(['docker','stats','--no-stream','--format','{{json .}}'],text=True).splitlines()
report['helper_resources']=[{k:e.get(k) for k in ('Name','CPUPerc','MemUsage','PIDs')} for line in stats for e in [json.loads(line)] if e.get('Name','').startswith('modish_v20_refresh_')]
report['test_db_waits']=subprocess.check_output(['docker','exec','modish_foundation_test_pg','psql','-X','-qAt','-U','modish_test','-d','modish_test','-c',"SELECT state,wait_event_type,wait_event,now()-xact_start FROM pg_stat_activity WHERE datname='modish_test' AND pid<>pg_backend_pid()"],text=True).splitlines()
query='SELECT count(*) FROM shopping_intents; SELECT source,max(last_seen_in_feed_at) FROM products GROUP BY source ORDER BY source'
report['test_catalogue_observations']=subprocess.check_output(['docker','exec','modish_foundation_test_pg','psql','-X','-qAt','-U','modish_test','-d','modish_test','-c',query],text=True).splitlines()
print(json.dumps(report))
""".replace('__R__', R).replace('__FRESH__',fresh_directory)
    else:
        code = """import json,time
from pathlib import Path
r=Path('__R__');previous=None
for attempt in range(720):
 progress=[]
 for name in ('__PREFIX__-runner.log','__FRESH__/download-errors.log','__FRESH__/import-errors.log','__FRESH__/readiness-errors.log'):
  path=r/name
  lines=path.read_text(errors='replace').splitlines() if path.exists() else []
  safe=[line[:1500] for line in lines if line.startswith(('fresh_','isolated_','readiness_profile_completed','{\"progress\":'))]
  progress.append({'file':name,'progress':safe[-4:]})
 encoded=json.dumps(progress)
 if encoded!=previous:print(encoded,flush=True);previous=encoded
 done=False
 for name in ('refresh-report.json','refresh-failure.json'):
  p=r/'__FRESH__'/name
  if p.exists():print(json.dumps({'final_report':json.loads(p.read_text())}),flush=True);done=True;break
 if done:break
 time.sleep(10)
""".replace('__R__', R).replace('__FRESH__',fresh_directory).replace('__PREFIX__',prefix)
    _, output, errors = client.exec_command('python3 -u -c ' + shlex.quote(code), timeout=7500 if operation == 'watch' else 45)
    if operation == 'watch':
        for line in output:
            print(line.rstrip(), flush=True)
        with client.open_sftp() as sftp:
            for name in ('refresh-report.json', 'refresh-failure.json', 'download-report.json',
                'import-report.json', 'inventory-report.json', 'readiness-report.json', 'before-import.json'):
                try:
                    with sftp.open(R + '/' + fresh_directory + '/' + name) as stream: value = json.loads(stream.read().decode())
                except (IOError, ValueError): continue
                (ROOT / 'docs/modish2' / ('intent-v20-fresh-' + ('20261013-' if renew else 'retry-20261010-' if retry else '') + name)).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        text_output = 'Watch finished'
    else:
        text_output = output.read().decode()
    exit_code = output.channel.recv_exit_status()
    print(json.dumps({'exit_code': exit_code, 'stdout': text_output, 'stderr': errors.read().decode()}).replace(password, '[REDACTED]'))
except Exception as error:
    print(json.dumps({'operation': operation, 'completed': False, 'error_type': type(error).__name__}))
    raise SystemExit(1) from None
finally:
    password = None
    client.close()
