"""Build and verify the updated image, then rerun the existing fresh clone only."""
from pathlib import Path
import subprocess
r=Path('/opt/modish-releases/20260922-foundation')
subprocess.run(['python3','-u',str(r/'audit-runner.py'),'--build-only'],check=True)
subprocess.run(['python3','-u',str(r/'profiles-runner.py')],check=True)
