from pathlib import Path
s=Path('scripts/prepare_styling_release_ssh.py').read_text(encoding='utf-8')
s=s.replace('sftp=c.open_sftp();sftp.mkdir(REMOTE,mode=0o700)','sftp=c.open_sftp()')
s=s.replace('scripts/prepare_styling_release_remote.py','scripts/recheck_styling_release_remote.py')
Path('scripts/recheck_styling_release_ssh.py').write_text(s,encoding='utf-8')
