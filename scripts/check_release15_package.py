"""Validate and run a pinned build15 archive in a fresh workspace copy, offline."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
from uuid import uuid4

import package_release15 as package


WORKSPACE = Path(__file__).resolve().parents[1]

# No application import occurs in this process. Both the runner and socket
# guard below are imported from the verified immutable extraction, not the
# working checkout. Device tests start fresh children with their own guard.
RUNNER = r'''
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

root = Path.cwd().resolve()
sys.path.insert(0, str(root / 'scripts'))
import run_device_fixture
run_device_fixture._install_loopback_network_guard()
import check_backend
summary = {}
original_run = unittest.TextTestRunner.run

def capture(runner, suite):
    result = original_run(runner, suite)
    summary.update(
        total=result.testsRun,
        skipped=len(result.skipped),
        executed=result.testsRun - len(result.skipped),
        errors=len(result.errors),
        failures=len(result.failures),
        expected_failures=len(result.expectedFailures),
        unexpected_successes=len(result.unexpectedSuccesses),
        skip_details=[{'test': test.id(), 'reason': reason}
                      for test, reason in result.skipped],
        successful=result.wasSuccessful(),
    )
    return result

with patch('dotenv.load_dotenv', return_value=False), patch.object(
    unittest.TextTestRunner, 'run', capture
):
    exit_code = check_backend.main()
summary['exit_code'] = exit_code
with (root / 'test-result.json').open('x', encoding='utf-8') as output:
    json.dump(summary, output, indent=2)
raise SystemExit(exit_code)
'''


def _unique_json(pairs):
    result = {}
    for key, value in pairs:
        package.require(key not in result, 'duplicate_manifest_key')
        result[key] = value
    return result


def _read_archive(path: Path, expected_sha: str, expected_commit: str):
    package.require(bool(re.fullmatch(r'[0-9a-f]{64}', expected_sha)),
                    'archive_sha256_required')
    package.require(bool(re.fullmatch(r'[0-9a-f]{40}', expected_commit)),
                    'full_git_commit_required')
    package.require(path.is_file() and not path.is_symlink()
                    and path.stat().st_size <= package.MAX_ARCHIVE,
                    'regular_bounded_archive_required')
    with path.open('rb') as source:
        digest = hashlib.file_digest(source, 'sha256').hexdigest()
        package.require(digest == expected_sha, 'archive_hash_mismatch')
        source.seek(0)
        with tarfile.open(fileobj=source, mode='r:') as archive:
            members = []
            total = 0
            for member in archive:
                package.require(len(members) < package.MAX_FILES + 1, 'archive_member_count_bound')
                package.require(member.isfile() and not member.pax_headers
                                and package.safe_name(member.name),
                                'regular_safe_archive_member_required')
                package.require(member.mode in {0o644, 0o755}, 'archive_member_mode')
                package.require(0 <= member.size <= package.MAX_MEMBER, 'archive_member_size_bound')
                total += member.size
                package.require(total <= package.MAX_ARCHIVE, 'archive_expansion_bound')
                members.append(member)
            names = [member.name for member in members]
            package.require(len(names) == len(set(names))
                            and len(names) == len({name.casefold() for name in names}),
                            'duplicate_archive_member')
            package.require(package.MANIFEST in names, 'manifest_missing')
            manifest_member = archive.getmember(package.MANIFEST)
            package.require(manifest_member.size <= package.MAX_MANIFEST, 'manifest_size_bound')
            manifest = json.loads(archive.extractfile(manifest_member).read(),
                                  object_pairs_hook=_unique_json)
            required = {
                'schema_version', 'release', 'commit', 'tree', 'base_image', 'base_migration',
                'migration', 'api_version', 'commit_epoch', 'expected_tests',
                'postgres_test_methods', 'verification_helpers', 'excluded_backend_files',
                'files', 'modes',
            }
            package.require(isinstance(manifest, dict) and set(manifest) == required,
                            'manifest_shape')
            package.require(manifest['schema_version'] == 1 and manifest['release'] == 'build15'
                            and manifest['commit'] == expected_commit
                            and bool(re.fullmatch(r'[0-9a-f]{40}', manifest['tree']))
                            and manifest['base_image'] == package.BASE_IMAGE
                            and manifest['base_migration'] == package.BASE_MIGRATION
                            and manifest['migration'] == package.MIGRATION
                            and manifest['api_version'] == package.API_VERSION,
                            'manifest_release_identity')
            package.require(type(manifest['commit_epoch']) is int
                            and manifest['commit_epoch'] >= 0
                            and type(manifest['expected_tests']) is int
                            and 0 < manifest['expected_tests'] <= 100000
                            and type(manifest['postgres_test_methods']) is int
                            and 0 <= manifest['postgres_test_methods']
                            <= manifest['expected_tests'],
                            'manifest_count_metadata')
            hashes, modes = manifest['files'], manifest['modes']
            package.require(isinstance(hashes, dict) and isinstance(modes, dict)
                            and set(hashes) == set(modes)
                            and set(names) == set(hashes) | {package.MANIFEST}
                            and manifest['verification_helpers']
                            == list(package.VERIFICATION_HELPERS),
                            'archive_manifest_members_mismatch')
            package.require(isinstance(manifest['excluded_backend_files'], list)
                            and all(isinstance(name, str) and package.safe_name(name)
                                    and name.startswith('backend/')
                                    and package.excluded_backend(name)
                                    for name in manifest['excluded_backend_files']),
                            'excluded_backend_metadata')
            files = {}
            for name, expected in hashes.items():
                package.require(package.allowed_payload(name)
                                and isinstance(expected, str)
                                and bool(re.fullmatch(r'[0-9a-f]{64}', expected)),
                                'invalid_payload_manifest_entry')
                member = archive.getmember(name)
                package.require(type(modes[name]) is int and member.mode == modes[name]
                                and member.mtime == manifest['commit_epoch'],
                                'member_metadata_mismatch')
                data = archive.extractfile(member).read()
                package.require(package.sha256(data) == expected, 'payload_hash_mismatch')
                files[name] = data
    pg_methods = package.validate_payload(files)
    package.require(pg_methods == manifest['postgres_test_methods'], 'postgres_metadata_mismatch')
    return manifest, files


def read_archive(path: Path, expected_sha: str, expected_commit: str):
    try:
        return _read_archive(path, expected_sha, expected_commit)
    except (tarfile.TarError, UnicodeError, ValueError, TypeError, KeyError,
            RecursionError):
        raise package.PackageError('invalid_archive_format_or_metadata') from None


def extract_verified(temporary: Path, manifest: dict, files: dict[str, bytes]) -> Path:
    temporary = temporary.resolve()
    package.require(temporary.is_dir() and not temporary.is_symlink(),
                    'regular_tmp_directory_required')
    target = temporary / ('release15-check-' + uuid4().hex)
    target.mkdir(mode=0o700, exist_ok=False)
    package.require(target.resolve().is_relative_to(temporary), 'extraction_scope')
    for name, data in files.items():
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        package.require(destination.resolve().is_relative_to(target), 'extraction_path_escape')
        with destination.open('xb') as output:
            output.write(data)
        destination.chmod(manifest['modes'][name])
    with (target / package.MANIFEST).open('x', encoding='utf-8') as output:
        json.dump(manifest, output, indent=2, sort_keys=True)
        output.write('\n')
    return target


def isolated_environment(target: Path) -> dict[str, str]:
    environment = {name: os.environ[name] for name in
                   ('SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'PATH') if name in os.environ}
    test_temp = target / 'test-temp'
    test_temp.mkdir()
    environment.update(
        DATABASE_URL='sqlite:///:memory:', JWT_SECRET='SYNTHETIC_ISOLATED_JWT',
        APP_ENV='test', PYTHON_DOTENV_DISABLED='1', PYTHONIOENCODING='utf-8',
        TEMP=str(test_temp), TMP=str(test_temp),
    )
    return environment


def run_backend(target: Path, manifest: dict, *, timeout: int = 1800) -> dict:
    environment = isolated_environment(target)
    log = target / 'backend-tests.log'
    with log.open('xb') as output:
        result = subprocess.run(
            [sys.executable, '-c', RUNNER], cwd=target, env=environment,
            stdout=output, stderr=subprocess.STDOUT, timeout=timeout,
        )
    result_path = target / 'test-result.json'
    package.require(result_path.is_file(), 'test_result_missing_see_private_log')
    summary = json.loads(result_path.read_text(encoding='utf-8'))
    expected_pg = manifest['postgres_test_methods']
    skips = summary.get('skip_details', [])
    counts_match = (
        summary.get('total') == manifest['expected_tests']
        and summary.get('skipped') == expected_pg
        and summary.get('executed') == manifest['expected_tests'] - expected_pg
        and summary.get('expected_failures') == 0
        and summary.get('unexpected_successes') == 0
        and len(skips) == expected_pg
        and all(item['test'].split('.', 1)[0].endswith('_postgres')
                and item['reason'] == 'Opt-in disposable PostgreSQL fixture' for item in skips)
    )
    return {
        'status': 'passed' if result.returncode == 0 and summary.get('successful')
                  and counts_match else 'failed',
        'discovered': summary.get('total'), 'executed': summary.get('executed'),
        'postgres_skipped': summary.get('skipped'), 'counts_match': counts_match,
        'errors': summary.get('errors'), 'failures': summary.get('failures'),
        'expected_failures': summary.get('expected_failures'),
        'external_network': 'blocked_loopback_only',
        'dotenv': 'disabled_before_application_imports',
        'runner': 'verified_archive_scripts/check_backend.py',
        'database': 'isolated_sqlite_not_production_postgres',
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', required=True, type=Path)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--commit', required=True)
    args = parser.parse_args()
    temporary = (WORKSPACE / '.tmp').resolve()
    package.require(temporary.is_relative_to(WORKSPACE), 'workspace_tmp_required')
    manifest, files = read_archive(args.archive, args.sha256, args.commit)
    target = extract_verified(temporary, manifest, files)
    report = {
        'status': 'checking', 'archive_sha256': args.sha256, 'commit': args.commit,
        'api_version': manifest['api_version'], 'migration': manifest['migration'],
        'verified_files': len(files), 'production_changed': False,
        'environment': 'local_isolated_sqlite_not_server_acceptance',
    }
    try:
        report['backend'] = run_backend(target, manifest)
        report['status'] = 'verified' if report['backend']['status'] == 'passed' else 'failed'
    except (package.PackageError, subprocess.TimeoutExpired) as error:
        report.update(status='failed', guard=str(error) if isinstance(error, package.PackageError)
                      else 'backend_test_timeout')
    with (target / 'package-check-report.json').open('x', encoding='utf-8') as output:
        json.dump(report, output, indent=2)
        output.write('\n')
    print(json.dumps({**report, 'verification_directory': str(target)}))
    return 0 if report['status'] == 'verified' else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except package.PackageError as error:
        print(json.dumps({'status': 'rejected', 'guard': str(error), 'production_changed': False}))
        raise SystemExit(1)
