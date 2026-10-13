"""Prepare fixed helpers for the final visual-QA corrections to release9."""
from pathlib import Path

root=Path(__file__).resolve().parent
old='/opt/modish-releases/20260917-release9'
new='/opt/modish-releases/20260920-release9-quality'
tag='modish-backend:release9-quality-20260920'
current_id='sha256:367fc511e3a521f576f12be39fec42cc200d49c60550c4a8b6b3f0683241ecdc'

def read(name): return (root/name).read_text(encoding='utf-8')
def write(name,text): (root/name).write_text(text,encoding='utf-8')

prepare=read('retest_release9_remote.py').replace(old,new).replace('modish-backend:release9-20260917',tag)
prepare=prepare.replace('sha256:0ed3f3b05ebaf071850706d2976423adfa21c6f0b09050b33f87503d2d71d871',current_id)
prepare=prepare.replace("str(R/'test-api.env')",repr(old+'/test-api.env'))
write('prepare_release9_quality_remote.py',prepare)

upload=read('retest_release9_ssh.py').replace(old,new)
upload=upload.replace("(root/'scripts/retest_release9_remote.py','retest.py')","(root/'scripts/prepare_release9_quality_remote.py','retest.py')")
upload=upload.replace("with c.open_sftp() as sftp:\n", "with c.open_sftp() as sftp:\n            try: sftp.mkdir(r,mode=0o700)\n            except IOError: sftp.stat(r)\n")
start="c.connect('194.87.118.236',username='root',password=secret,look_for_keys=False,allow_agent=False,timeout=15,auth_timeout=15)"
replacement="remote_socket=socket.create_connection(('194.87.118.236',22),timeout=30)\n        remote_socket.settimeout(30)\n        if not remote_socket.recv(256,socket.MSG_PEEK).startswith(b'SSH-'): raise RuntimeError('SSH banner unavailable')\n        c.connect('194.87.118.236',username='root',password=secret,look_for_keys=False,allow_agent=False,sock=remote_socket,timeout=30,banner_timeout=30,auth_timeout=15)"
assert start in upload
write('prepare_release9_quality_ssh.py',upload.replace(start,replacement))

activate=read('activate_release9.py').replace(old,new).replace('/var/backups/modish/20260917-release9','/var/backups/modish/20260920-release9-quality')
activate=activate.replace("OLD='modish-backend:release7-20260916'","OLD='modish-backend:release9-20260917'")
activate=activate.replace("NEW='modish-backend:release9-20260917'",f"NEW='{tag}'")
activate=activate.replace('sha256:0ed3f3b05ebaf071850706d2976423adfa21c6f0b09050b33f87503d2d71d871',current_id)
activate=activate.replace('os.umask(0o077)','os.umask(0o077)\n    B.mkdir(mode=0o700,parents=True,exist_ok=True)')
write('activate_release9_quality.py',activate)
write('activate_release9_quality_ssh.py',read('activate_release9_ssh.py').replace(old,new).replace("scripts/activate_release9.py","scripts/activate_release9_quality.py"))
for name in ('preview','verify','fetch_release9_report'):
    source=f'{name}_release9_ssh.py' if name!='fetch_release9_report' else 'fetch_release9_report_ssh.py'
    target=source.replace('release9','release9_quality')
    write(target,read(source).replace(old,new))

cleanup=read('finalize_release9_ssh.py').replace(old,new).replace('modish-backend:release9-20260917',tag)
cleanup=cleanup.replace('p=r/name',f'p=Path({old!r})/name').replace('assert p.resolve().parent==r',f'assert p.resolve().parent==Path({old!r})')
write('finalize_release9_quality_ssh.py',cleanup)
print('Fixed release9 quality helpers generated')
