"""One fixed read-only SSH verification. No command or upload interface."""
import json,os,socket,sys
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0,os.path.join(os.environ['TEMP'],'modish-ssh-runtime'))
import paramiko
command="""python3 - <<'PY'
import json,subprocess
from pathlib import Path
r=Path('/opt/modish-releases/20260913-catalog-fixes')
p=r/'deployment-record.json'
report=json.loads(p.read_text()) if p.exists() else {'status':'pending'}
current=json.loads(subprocess.check_output(['docker','inspect','modish_backend']))[0]
result={'report':report,'current_tag':current['Config']['Image'],'current_image':current['Image']}
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
