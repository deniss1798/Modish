"""Verify the exact allowlisted archive locally; never connects to the server."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--report', default='foundation-package-check.json')
    parser.add_argument('--archive', default='modish-foundation.tar')
    args = parser.parse_args()
    report_path = (ROOT / 'docs/modish2' / args.report).resolve()
    assert report_path.is_relative_to((ROOT / 'docs/modish2').resolve())
    archive_path = (ROOT / '.tmp' / args.archive).resolve()
    assert archive_path.is_relative_to((ROOT / '.tmp').resolve())
    sha = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    workspace = (ROOT / '.tmp').resolve()
    target = Path(tempfile.mkdtemp(prefix='foundation-package-check-', dir=workspace)).resolve()
    assert target.is_relative_to(workspace)
    # Keep this exact test checkout for inspection; do not remove another run.
    with tarfile.open(archive_path) as archive:
        manifest = json.load(archive.extractfile('source-manifest.json'))
        for name, expected in manifest['files'].items():
            member = archive.getmember(name)
            assert member.isfile(), name
            destination = (target / name).resolve()
            assert destination.is_relative_to(target), name
            data = archive.extractfile(member).read()
            assert hashlib.sha256(data).hexdigest() == expected, name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
    log_path = workspace / 'continuation-package-tests.log'
    environment = {name: os.environ[name] for name in ('SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'PATH') if name in os.environ}
    test_temp = target / 'test-temp'
    test_temp.mkdir()
    environment.update(TEMP=str(test_temp), TMP=str(test_temp))
    environment.update(DATABASE_URL='sqlite:///:memory:', JWT_SECRET='SYNTHETIC_ISOLATED_JWT',
                       APP_ENV='test', PYTHON_DOTENV_DISABLED='1', PYTHONIOENCODING='utf-8')
    with log_path.open('w', encoding='utf-8') as log:
        result = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_*.py', '-v'],
                                cwd=target / 'backend', env=environment, stdout=log, stderr=subprocess.STDOUT)
    lines = log_path.read_text(encoding='utf-8', errors='replace').splitlines()
    report = {'archive_sha256': sha, 'verified_files': len(manifest['files']),
              'environment': 'local_windows_sqlite_not_server_docker',
              'test_exit_code': result.returncode,
              'test_summary': next((line for line in reversed(lines) if line.startswith('Ran ')), None),
              'production_changed': False}
    report_path.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))
    if result.returncode:
        print(json.dumps({'failure_tail': lines[-60:]}, ensure_ascii=True))
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
