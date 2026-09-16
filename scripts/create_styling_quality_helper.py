from pathlib import Path
s=Path('scripts/recheck_styling_release_ssh.py').read_text(encoding='utf-8')
s=s.replace('scripts/recheck_styling_release_remote.py','scripts/recheck_styling_quality_remote.py')
s=s.replace('smoke_styling_fixes.py','smoke_styling_quality.py')
Path('scripts/recheck_styling_quality_ssh.py').write_text(s,encoding='utf-8')
