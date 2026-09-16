"""Fetch only public release evidence and synthetic-test results from the fixed release."""
import json,os,socket,sys
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0,os.path.join(os.environ['TEMP'],'modish-ssh-runtime'))
import paramiko
ROOT=Path(__file__).resolve().parents[1]
command="""python3 - <<'PY'
import json,subprocess
from pathlib import Path
r=Path('/opt/modish-releases/20260913-styling-fixes')
deployment=json.loads((r/'deployment-record.json').read_text())
assert deployment['status']=='deployed'
smoke=[json.loads(x) for x in (r/'smoke.log').read_text().splitlines() if x.startswith('{')][-1]
assert smoke['result']=='passed'
current=json.loads(subprocess.check_output(['docker','inspect','modish_backend']))[0]
assert current['Image']==deployment['image_id']
print(json.dumps({'deployment':deployment,'smoke':smoke,'manifest':json.loads((r/'source-manifest.json').read_text()),'cleanup':json.loads((r/'cleanup-record.json').read_text())}))
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
  output=o.read().decode();error=e.read().decode();code=o.channel.recv_exit_status()
  if code==0:
   report=json.loads(output)
   target=ROOT/'docs/modish2/styling_fixes_deployment_record.json'
   target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
   result={'code':0,'report':str(target),'deployment':report['deployment'],'cleanup':report['cleanup']}
  else:result={'code':code,'error':error}
  sock.sendall(json.dumps(result).replace(secret,'[REDACTED]').encode()+b'\n')
 finally:c.close()
