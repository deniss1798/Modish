"""Fetch only synthetic-profile QA output, never accounts or credentials."""
import json,os,socket,sys
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0,os.path.join(os.environ['TEMP'],'modish-ssh-runtime'))
import paramiko
root=Path(__file__).resolve().parents[1]
command="""python3 - <<'PY'
import json
from pathlib import Path
r=Path('/opt/modish-releases/20260920-release9-quality')
rows=[json.loads(x) for x in (r/'smoke.log').read_text().splitlines() if x.startswith('{')]
last=rows[-1]
if last.get('result')=='passed': print(json.dumps(last))
else: print(json.dumps({'pending_or_failed':rows[-3:],'tail':(r/'smoke.log').read_text().splitlines()[-12:]}))
PY"""
listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(1);listener.settimeout(120)
print(listener.getsockname()[1],flush=True)
sock,_=listener.accept();listener.close()
with sock,sock.makefile('r',encoding='utf-8') as incoming:
    secret=json.loads(incoming.readline())['password']
    c=paramiko.SSHClient();c.load_host_keys(os.path.expanduser('~/.ssh/known_hosts'))
    try:
        from release9_ssh_connection import ready_socket
        remote_socket=ready_socket()
        c.connect('194.87.118.236',username='root',password=secret,look_for_keys=False,allow_agent=False,sock=remote_socket,timeout=30,banner_timeout=30,auth_timeout=15)
        i,o,e=c.exec_command(command,timeout=30)
        output=o.read().decode();error=e.read().decode();code=o.channel.recv_exit_status()
        if code==0:
            data=json.loads(output)
            if data.get('result')=='passed':
                (root/'docs/modish2/release9_smoke.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
                result={'result':'passed','profiles':[{k:v for k,v in p.items() if k!='preview'} for p in data['profiles']]}
            else:result=data
        else:result={'code':code,'error':error}
        sock.sendall(json.dumps(result).replace(secret,'[REDACTED]').encode()+b'\n')
    finally:c.close()
