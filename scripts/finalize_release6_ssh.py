"""One fixed read-only SSH verification. No command or upload interface."""
import json,os,socket,sys
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0,os.path.join(os.environ['TEMP'],'modish-ssh-runtime'))
import paramiko
command="""python3 - <<'PY'
import json,subprocess
from pathlib import Path
r=Path('/opt/modish-releases/20260915-release6')
assert r.resolve()==r and json.loads((r/'deployment-record.json').read_text())['status']=='deployed'
def inspect(name):return json.loads(subprocess.check_output(['docker','inspect',name]))[0]
live={n:inspect(n)['Id'] for n in ('modish_backend','modish_postgres','modish_redis')}
assert inspect('modish_backend')['Config']['Image']=='modish-backend:release6-20260915'
test=inspect('modish_release6_test_pg')
assert test['Name']=='/modish_release6_test_pg'
assert set(test['NetworkSettings']['Networks'])=={'modish-release6-test'}
assert test['Id'] not in live.values()
assert all(m['Type']=='volume' and m['Destination']=='/var/lib/postgresql/data' for m in test['Mounts'])
production_mounts={m.get('Name',m['Source']) for n in ('modish_postgres','modish_redis') for m in inspect(n)['Mounts']}
assert all(m['Name'] not in production_mounts for m in test['Mounts'])
subprocess.run(['docker','rm','--force','--volumes','modish_release6_test_pg'],check=True,stdout=subprocess.DEVNULL)
network=json.loads(subprocess.check_output(['docker','network','inspect','modish-release6-test']))[0]
assert not network['Containers']
subprocess.run(['docker','network','rm','modish-release6-test'],check=True,stdout=subprocess.DEVNULL)
for name in ('test-api.env','test-postgres.env'):
 p=r/name
 assert p.resolve().parent==r
 p.unlink(missing_ok=True)
assert all(inspect(n)['Id']==cid for n,cid in live.items())
result={'temporary_test_database_removed':True,'test_secrets_removed':True,'production_containers_preserved':True}
(r/'cleanup-record.json').write_text(json.dumps(result))
print(json.dumps(result))
PY"""
listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(1);listener.settimeout(120)
print(listener.getsockname()[1],flush=True)
sock,_=listener.accept();listener.close()
with sock,sock.makefile('r',encoding='utf-8') as incoming:
 secret=json.loads(incoming.readline())['password']
 c=paramiko.SSHClient();c.load_host_keys(os.path.expanduser('~/.ssh/known_hosts'))
 try:
  c.connect('194.87.118.236',username='root',password=secret,look_for_keys=False,allow_agent=False,timeout=15,auth_timeout=15)
  i,o,e=c.exec_command(command,timeout=45)
  out=o.read().decode('utf-8');err=e.read().decode('utf-8');code=o.channel.recv_exit_status()
  response=json.dumps({'stdout':out,'stderr':err,'code':code}).replace(secret,'[REDACTED]')
  sock.sendall(response.encode()+b'\n')
 finally:c.close()
