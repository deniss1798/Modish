"""One fixed upload/build/test operation; accepts only an in-memory credential."""
import json,os,socket,sys,hashlib
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0,os.path.join(os.environ['TEMP'],'modish-ssh-runtime'))
import paramiko
ROOT=Path(__file__).resolve().parents[1]
REMOTE='/opt/modish-releases/20260917-release9'
listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(1);listener.settimeout(120)
print(listener.getsockname()[1],flush=True)
sock,_=listener.accept();listener.close()
with sock,sock.makefile('r',encoding='utf-8') as incoming:
 secret=json.loads(incoming.readline())['password']
 c=paramiko.SSHClient();c.load_host_keys(os.path.expanduser('~/.ssh/known_hosts'))
 try:
  remote_socket=socket.create_connection(('194.87.118.236',22),timeout=30)
  remote_socket.settimeout(30)
  if not remote_socket.recv(256,socket.MSG_PEEK).startswith(b'SSH-'): raise RuntimeError('SSH banner unavailable')
  c.connect('194.87.118.236',username='root',password=secret,look_for_keys=False,allow_agent=False,sock=remote_socket,timeout=30,banner_timeout=30,auth_timeout=15)
  sftp=c.open_sftp()
  uploads=[(ROOT/'scripts/activate_release9.py','activate.py')]
  for local,name in uploads:sftp.put(str(local),REMOTE+'/'+name);sftp.chmod(REMOTE+'/'+name,0o600)
  sftp.close()
  command="python3 - <<'PY'\nimport subprocess\nfrom pathlib import Path\nr=Path('/opt/modish-releases/20260917-release9')\nwith (r/'activation.log').open('w') as log:\n p=subprocess.Popen(['python3','-u',str(r/'activate.py')],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)\nprint('Preparation started:',p.pid)\nPY"
  i,o,e=c.exec_command(command,timeout=30)
  result={'stdout':o.read().decode(),'stderr':e.read().decode(),'code':o.channel.recv_exit_status()}
  sock.sendall(json.dumps(result).replace(secret,'[REDACTED]').encode()+b'\n')
 finally:c.close()
