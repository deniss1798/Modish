"""Fixed read-only catalogue audit of release9 isolated clone; no live writes."""
import json, os, socket, sys
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0,os.path.join(os.environ['TEMP'],'modish-ssh-runtime'))
import paramiko
root=Path(__file__).resolve().parents[1]
r='/opt/modish-releases/20260917-release9'
listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(1);listener.settimeout(120)
print(listener.getsockname()[1],flush=True)
sock,_=listener.accept();listener.close()
with sock,sock.makefile('r',encoding='utf-8') as incoming:
    secret=json.loads(incoming.readline())['password']
    c=paramiko.SSHClient();c.load_host_keys(os.path.expanduser('~/.ssh/known_hosts'))
    try:
        c.connect('194.87.118.236',username='root',password=secret,look_for_keys=False,allow_agent=False,timeout=15,auth_timeout=15)
        with c.open_sftp() as sftp:
            sftp.put(str(root/'scripts/audit_release9_catalog.py'),r+'/audit.py');sftp.chmod(r+'/audit.py',0o600)
        command='docker run --rm --network modish-release9-test --env-file '+r+'/test-api.env -v '+r+'/audit.py:/audit.py:ro --entrypoint python modish-backend:release9-20260917 /audit.py'
        i,o,e=c.exec_command(command,timeout=40)
        result={'stdout':o.read().decode(),'stderr':e.read().decode(),'code':o.channel.recv_exit_status()}
        sock.sendall(json.dumps(result).replace(secret,'[REDACTED]').encode()+b'\n')
    finally:c.close()
