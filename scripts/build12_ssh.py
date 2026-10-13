"""Fixed release actions; SSH password is accepted only via a local memory socket."""
import json
import os
from pathlib import Path
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.tmp/ssh-runtime'))
import paramiko
from release9_ssh_connection import ready_socket

REMOTE = '/opt/modish-releases/20261006-build12'
operation = sys.argv[1]
assert operation in {'inspect', 'prepare', 'status', 'activate', 'fetch'}
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
        if operation == 'inspect':
            command = "python3 - <<'PY'\nimport json,subprocess\nfor name in ('modish_backend','modish_postgres','modish_redis'):\n d=json.loads(subprocess.check_output(['docker','inspect',name]))[0]\n print(json.dumps({'name':name,'image':d['Image'],'tag':d['Config']['Image'],'status':d['State']['Status']}))\nPY"
        elif operation == 'prepare':
            with client.open_sftp() as sftp:
                try: sftp.mkdir(REMOTE, mode=0o700)
                except IOError: sftp.stat(REMOTE)
                for source, name in [(ROOT / '.tmp/modish-build12-server.tar', 'source.tar'),
                                     (ROOT / 'scripts/build12_remote.py', 'release.py'),
                                     (ROOT / 'scripts/smoke_build11.py', 'smoke.py')]:
                    sftp.put(str(source), REMOTE + '/' + name)
                    sftp.chmod(REMOTE + '/' + name, 0o600)
            command = "python3 - <<'PY'\nimport subprocess\nfrom pathlib import Path\nr=Path('" + REMOTE + "')\nwith (r/'prepare.log').open('w') as log:\n p=subprocess.Popen(['python3','-u',str(r/'release.py'),'prepare'],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)\nprint('Preparation started:',p.pid)\nPY"
        elif operation == 'activate':
            command = "python3 - <<'PY'\nimport subprocess\nfrom pathlib import Path\nr=Path('" + REMOTE + "')\nwith (r/'activation.log').open('w') as log:\n p=subprocess.Popen(['python3','-u',str(r/'release.py'),'activate'],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)\nprint('Activation started:',p.pid)\nPY"
        elif operation == 'status':
            command = "python3 - <<'PY'\nimport json\nfrom pathlib import Path\nr=Path('" + REMOTE + "')\nfor name in ('prepare.log','unit-tests.log','smoke.log','activation.log','deployment-record.json'):\n p=r/name\n if p.exists(): print(json.dumps({'file':name,'tail':p.read_text()[-4500:]}))\nPY"
        else:
            with client.open_sftp() as sftp:
                for name in ('deployment-record.json', 'smoke-report.json'):
                    sftp.get(REMOTE + '/' + name, str(ROOT / 'docs/modish2' / ('build12-' + name)))
            command = "printf 'Deployment records downloaded\n'"
        _, output, error = client.exec_command(command, timeout=40)
        result = {'stdout': output.read().decode(), 'stderr': error.read().decode(),
                  'code': output.channel.recv_exit_status()}
        sock.sendall(json.dumps(result).replace(secret, '[REDACTED]').encode() + b'\n')
    except Exception as exc:
        sock.sendall(json.dumps({'error': type(exc).__name__, 'message': str(exc)[:300]}).replace(secret, '[REDACTED]').encode() + b'\n')
    finally:
        client.close()
