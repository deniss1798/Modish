"""Build reviewed catalog rules and audit with a read-only database transaction."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
import tarfile

R = Path('/opt/modish-releases/20260922-foundation')
BASE = 'sha256:4ae234fc8a1f8082bb368f4f617d187a6138bb5848c02af5e9a7a9f15a3d6d7e'
TAG = 'modish-backend:foundation-20260922'


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def inspect(name):
    return json.loads(subprocess.check_output(['docker', 'inspect', name]))[0]


def main():
    assert inspect('modish_backend')['Image'] == BASE
    with tarfile.open(R/'source.tar') as archive:
        archive.extractall(R, filter='data')
    manifest = json.loads((R/'source-manifest.json').read_text())
    assert manifest['base_image'] == BASE
    assert all(hashlib.sha256((R/n).read_bytes()).hexdigest() == sha for n, sha in manifest['files'].items())
    # Tar members have deterministic timestamps. Refresh the build context's
    # metadata so BuildKit cannot reuse an equal-size, same-mtime previous file.
    for name in manifest['files']:
        os.utime(R/name, None)
    backend = (R/'backend').resolve()
    for bytecode in backend.rglob('*.pyc'):
        assert bytecode.resolve().is_relative_to(backend)
        bytecode.unlink()
    with (R/'build.log').open('w') as log:
        run('docker', 'build', '-t', TAG, str(R/'backend'), stdout=log, stderr=subprocess.STDOUT)
    print('image_built', flush=True)
    verify = "import json,sys,hashlib;from pathlib import Path;m=json.load(sys.stdin);bad=[n for n,s in m['files'].items() if hashlib.sha256(Path('/app/'+n.removeprefix('backend/')).read_bytes()).hexdigest()!=s];assert not bad,bad;print('image_sources_verified')"
    subprocess.run(['docker','run','--rm','-i','--network','none','--entrypoint','python',TAG,'-c',verify],
                   input=json.dumps(manifest).encode(),check=True)
    with (R/'unit-tests.log').open('w') as log:
        run('docker', 'run', '--rm', '--network', 'none', '-e', 'DATABASE_URL=sqlite:///:memory:',
            '-e', 'JWT_SECRET=SYNTHETIC_ISOLATED_JWT', '-e', 'PYTHON_DOTENV_DISABLED=1',
            '--entrypoint', 'python', TAG, '-m', 'unittest', 'discover', '-s', 'tests', '-v',
            stdout=log, stderr=subprocess.STDOUT)
    print('unit_tests_passed', flush=True)
    if '--build-only' in sys.argv:
        return
    with (R/'audit-report.json').open('w') as output, (R/'audit-errors.log').open('w') as errors:
        run('docker', 'run', '--rm', '--network', 'modish_default', '--env-file', '/opt/Modish/backend/.env',
            '-v', str(R/'audit.py')+':/audit.py:ro', '-e', 'PYTHONPATH=/app',
            '--entrypoint', 'python', TAG, '/audit.py', stdout=output, stderr=errors)
    report = json.loads((R/'audit-report.json').read_text())
    assert report['transaction_read_only']
    assert inspect('modish_backend')['Image'] == BASE
    print('read_only_audit_complete', flush=True)


if __name__ == '__main__':
    main()
