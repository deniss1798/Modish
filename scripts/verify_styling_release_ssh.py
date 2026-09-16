"""One fixed read-only release status query. No command/upload interface."""
import json,os,socket,sys
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0,os.path.join(os.environ['TEMP'],'modish-ssh-runtime'))
import paramiko
command="""python3 - <<'PY'
import json,subprocess
from pathlib import Path
r=Path('/opt/modish-releases/20260913-styling-fixes')
result={}
for name in ('prepare.log','unit-tests.log','smoke.log','activation.log','deployment-record.json'):
 p=r/name
 if p.exists():
  lines=p.read_text().splitlines()
  result[name]=lines[-8:]
  if name=='smoke.log':
   result[name]=[line for line in lines if line.startswith('{')][-8:]
   if any('"result": "failed"' in line for line in lines): result['smoke_error_context']=lines[-12:]
  if name=='unit-tests.log':
   result[name]=[line for line in lines if ' ... FAIL' in line or 'FAIL:' in line]+lines[-22:]
current=json.loads(subprocess.check_output(['docker','inspect','modish_backend']))[0]
result['current_tag']=current['Config']['Image']
result['current_image']=current['Image']
print(json.dumps(result))
PY"""
listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(1);listener.settimeout(120)
print(listener.getsockname()[1],flush=True)
sock,_=listener.accept();listener.close()
with sock,sock.makefile('r',encoding='utf-8') as incoming:
 secret=json.loads(incoming.readline())['password']
 c=paramiko.SSHClient();c.load_host_keys(os.path.expanduser('~/.ssh/known_hosts'))
 try:
  c.connect('194.87.118.236',username='root',password=secret,look_for_keys=False,allow_agent=False,timeout=15,banner_timeout=25,auth_timeout=15)
  i,o,e=c.exec_command(command,timeout=40)
  result={'stdout':o.read().decode(),'stderr':e.read().decode(),'code':o.channel.recv_exit_status()}
  sock.sendall(json.dumps(result).replace(secret,'[REDACTED]').encode()+b'\n')
 finally:c.close()
