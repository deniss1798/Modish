"""Offline regressions for commit pinning, archive admission and truthful counts."""
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import check_release15_package as checker
import package_release15 as package


WORKSPACE = Path(__file__).resolve().parents[1]
RUNNER_SOURCE = b'''from pathlib import Path
import os
import sys
import unittest

def main():
    import dotenv
    assert dotenv.load_dotenv() is False
    assert 'PARENT_PRIVATE_SENTINEL' not in os.environ
    assert 'MODISH_ISOLATED_PG_RUNTIME' not in os.environ
    assert os.environ['DATABASE_URL'] == 'sqlite:///:memory:'
    root = Path(__file__).resolve().parents[1]
    os.chdir(root / 'backend')
    sys.path.insert(0, str(root / 'backend'))
    suite = unittest.defaultTestLoader.discover('tests')
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1
'''
TEST_SOURCE = b'''import socket
import unittest

class SanityTests(unittest.TestCase):
    def test_socket_guard_from_extraction(self):
        with self.assertRaises(PermissionError):
            socket.getaddrinfo('never-connect.invalid', 443)
'''


class Release15PackageTests(unittest.TestCase):
    def setUp(self):
        temporary = WORKSPACE / '.tmp'
        temporary.mkdir(exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(prefix='release15-unit-', dir=temporary)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.repository = self.root / 'repository'
        self.repository.mkdir()
        self.git('init', '-q')
        self.files = self.minimal_payload()
        for name, data in self.files.items():
            self.write(name, data)
        self.commit = self.commit_files()

    def git(self, *args, input=None):
        result = subprocess.run(
            ['git', '-C', str(self.repository), '-c', 'core.autocrlf=false',
             '-c', 'core.hooksPath=', '-c', 'user.name=Synthetic Fixture',
             '-c', 'user.email=fixture@example.com', '-c', 'commit.gpgsign=false', *args],
            input=input, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
        )
        return result.stdout

    def write(self, name, data):
        path = self.repository / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def commit_files(self):
        self.git('add', '--all')
        self.git('commit', '-qm', 'Synthetic package fixture')
        return self.git('rev-parse', 'HEAD').decode().strip()

    def minimal_payload(self):
        files = {
            'backend/app/main.py': b'API_VERSION = "1.0.1-build15"\n',
            'backend/requirements.txt': b'fastapi==0.115.6\n',
            'backend/tests/test_sanity.py': TEST_SOURCE,
        }
        revisions = [
            (package.BASE_MIGRATION, None),
            ('20261015_0026', package.BASE_MIGRATION),
            ('20261015_0027', '20261015_0026'),
            (package.MIGRATION, '20261015_0027'),
        ]
        for revision, parent in revisions:
            files[f'backend/alembic/versions/{revision}_fixture.py'] = (
                f'revision = {revision!r}\ndown_revision = {parent!r}\n'.encode()
            )
        for name in package.VERIFICATION_HELPERS:
            files[name] = (WORKSPACE / name).read_bytes()
        files['scripts/check_backend.py'] = RUNNER_SOURCE
        return files

    def build(self, *, commit=None, name='source.tar', expected_tests=1):
        archive = self.root / name
        result = package.build_package(self.repository, commit or self.commit, archive,
                                       expected_tests=expected_tests)
        return archive, result

    def admitted(self, archive, result):
        return checker.read_archive(archive, result['archive_sha256'], result['commit'])

    def changed_archive(self, *, mutate=None, extra=None, manifest_change=None):
        archive, _ = self.build()
        with tarfile.open(archive) as source:
            entries = [(member, source.extractfile(member).read()) for member in source]
        result = self.root / 'changed.tar'
        with tarfile.open(result, 'w', format=tarfile.USTAR_FORMAT) as destination:
            for original, data in entries:
                member = deepcopy(original)
                if manifest_change and member.name == package.MANIFEST:
                    data = manifest_change(data)
                if mutate:
                    member, data = mutate(member, data)
                member.size = len(data)
                destination.addfile(member, io.BytesIO(data))
            if extra:
                member, data = extra(entries)
                destination.addfile(member, io.BytesIO(data) if member.isfile() else None)
        return result

    def reject_archive(self, archive, code):
        with self.assertRaisesRegex(package.PackageError, code):
            checker.read_archive(archive, package.sha256(archive.read_bytes()), self.commit)

    def test_committed_bytes_ignore_modified_and_untracked_working_files(self):
        self.write('backend/app/main.py', b'raise RuntimeError("working tree must not run")\n')
        self.write('backend/app/untracked.py', b'not_part_of_commit = True\n')
        archive, result = self.build()
        manifest, files = self.admitted(archive, result)
        self.assertEqual(files, self.files)
        self.assertEqual(manifest['commit'], self.commit)
        self.assertEqual(manifest['api_version'], '1.0.1-build15')
        self.assertNotIn('backend/app/untracked.py', files)

    def test_archive_is_reproducible_and_existing_artifact_is_never_overwritten(self):
        archive, result = self.build()
        before = archive.read_bytes()
        with self.assertRaises(FileExistsError):
            self.build()
        self.assertEqual(archive.read_bytes(), before)
        second, again = self.build(name='second.tar')
        self.assertEqual(second.read_bytes(), before)
        self.assertEqual(result['archive_sha256'], again['archive_sha256'])

    def test_full_commit_and_committed_build15_version_are_required(self):
        for commit in ('HEAD', self.commit[:12], self.commit.upper()):
            with self.subTest(commit=commit), self.assertRaises(package.PackageError):
                self.build(commit=commit)
        self.write('backend/app/main.py', b'API_VERSION="1.0.0-build14"\n')
        newer = self.commit_files()
        with self.assertRaisesRegex(package.PackageError, 'build15_api_version'):
            self.build(commit=newer)

    def test_private_generated_and_binary_extensions_are_excluded_before_blob_reads(self):
        excluded = ('backend/.env', 'backend/.env.production', 'backend/.venv/secret.py',
                    'backend/cache.pyc', 'backend/private.key', 'backend/image.png')
        for name in excluded:
            self.write(name, b'\0PRIVATE_NOT_FOR_RELEASE')
        newer = self.commit_files()
        archive, result = self.build(commit=newer)
        manifest, files = self.admitted(archive, result)
        self.assertEqual(manifest['excluded_backend_files'], sorted(excluded))
        self.assertEqual(files, self.files)

    def test_unknown_binary_content_is_rejected_instead_of_silently_omitted(self):
        self.write('backend/unexpected.bin', b'\0unrecognized')
        with self.assertRaisesRegex(package.PackageError, 'binary_backend_payload'):
            self.build(commit=self.commit_files())

    def test_required_helper_and_git_symlink_are_not_admitted(self):
        self.git('rm', '-q', 'scripts/check_backend.py')
        self.git('commit', '-qm', 'Remove required helper')
        missing = self.git('rev-parse', 'HEAD').decode().strip()
        with self.assertRaisesRegex(package.PackageError, 'verification_helper_missing'):
            self.build(commit=missing)
        self.git('reset', '--hard', '-q', self.commit)
        oid = self.git('hash-object', '-w', '--stdin', input=b'../private').decode().strip()
        self.git('update-index', '--add', '--cacheinfo', '120000', oid, 'backend/app/link.py')
        self.git('commit', '-qm', 'Synthetic symlink')
        linked = self.git('rev-parse', 'HEAD').decode().strip()
        with self.assertRaisesRegex(package.PackageError, 'regular_allowlisted_git_blob'):
            self.build(commit=linked)

    def test_migration_duplicate_dangling_cycle_and_extra_head_are_rejected(self):
        variants = {
            'duplicate': b"revision='20261015_0028'\ndown_revision=None\n",
            'dangling': b"revision='other'\ndown_revision='missing'\n",
            'cycle': b"revision='other'\ndown_revision='other'\n",
            'head': b"revision='other'\ndown_revision='20261013_0025'\n",
        }
        for kind, data in variants.items():
            with self.subTest(kind=kind), self.assertRaises(package.PackageError):
                package.validate_source_metadata({
                    **self.files, 'backend/alembic/versions/extra.py': data,
                })

    def test_wrong_external_hash_is_rejected_before_any_python_is_executed(self):
        archive, result = self.build()
        with self.assertRaisesRegex(package.PackageError, 'archive_hash_mismatch'):
            checker.read_archive(archive, '0' * 64, self.commit)
        with self.assertRaisesRegex(package.PackageError, 'manifest_release_identity'):
            checker.read_archive(archive, result['archive_sha256'], '0' * 40)

    def test_archive_validation_does_not_execute_its_python_helpers(self):
        self.write('scripts/scan_secrets.py', b'raise AssertionError("Do not execute archive")\n')
        archive, result = self.build(commit=self.commit_files())
        _manifest, files = self.admitted(archive, result)
        self.assertIn(b'raise AssertionError', files['scripts/scan_secrets.py'])

    def test_tampered_member_hash_and_duplicate_members_are_rejected(self):
        def changed(member, data):
            return member, data + b'\n' if member.name == 'backend/app/main.py' else data

        self.reject_archive(self.changed_archive(mutate=changed), 'payload_hash_mismatch')
        (self.root / 'source.tar').unlink()
        duplicate = self.changed_archive(extra=lambda entries: entries[0])
        self.reject_archive(duplicate, 'duplicate_archive_member')

    def test_unsafe_paths_case_collisions_and_nonregular_members_are_rejected(self):
        paths = ('../escape.py', '/absolute.py', 'backend\\escape.py', 'backend/NUL.py')
        for index, name in enumerate(paths):
            with self.subTest(name=name):
                def extra(_entries):
                    member = tarfile.TarInfo(name)
                    return member, b''
                archive = self.changed_archive(extra=extra)
                self.reject_archive(archive, 'regular_safe_archive_member')
                (self.root / 'source.tar').unlink()
                archive.rename(self.root / f'unsafe-{index}.tar')
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.DIRTYPE):
            with self.subTest(kind=kind):
                def extra(_entries):
                    member = tarfile.TarInfo('backend/link.py')
                    member.type, member.linkname = kind, '../outside'
                    return member, b''
                archive = self.changed_archive(extra=extra)
                self.reject_archive(archive, 'regular_safe_archive_member')
                (self.root / 'source.tar').unlink()
                archive.unlink()
        def collision(entries):
            member, data = deepcopy(entries[0])
            member.name = member.name.upper()
            return member, data
        self.reject_archive(self.changed_archive(extra=collision), 'duplicate_archive_member')

    def test_extra_unmanifested_file_duplicate_json_key_and_size_bound_are_rejected(self):
        def extra(_entries):
            return tarfile.TarInfo('scripts/unapproved.py'), b''
        self.reject_archive(self.changed_archive(extra=extra), 'archive_manifest_members')
        (self.root / 'source.tar').unlink()
        changed = self.changed_archive(manifest_change=lambda data: data.replace(
            b'"release": "build15",', b'"release": "build15", "release": "build15",'))
        self.reject_archive(changed, 'duplicate_manifest_key')
        with patch.object(package, 'MAX_MEMBER', 1):
            self.reject_archive(changed, 'archive_member_size_bound')

    def test_credentials_are_rejected_without_returning_the_value(self):
        value = ('secret' + 'literal' + '1234567890').encode()
        assignment = b'API_' + b'KEY="' + value + b'"\n'
        self.write('backend/config.txt', assignment)
        with self.assertRaisesRegex(package.PackageError, 'credential_guard') as caught:
            self.build(commit=self.commit_files())
        self.assertNotIn(value.decode(), str(caught.exception))

    def test_verified_runner_uses_extraction_cleared_environment_and_truthful_counts(self):
        archive, result = self.build()
        manifest, files = self.admitted(archive, result)
        target = checker.extract_verified(self.root, manifest, files)
        with patch.dict(os.environ, {'PARENT_PRIVATE_SENTINEL': 'not-for-child',
                                   'MODISH_ISOLATED_PG_RUNTIME': 'never-connect'}):
            report = checker.run_backend(target, manifest)
        self.assertEqual(report['status'], 'passed')
        self.assertEqual((report['discovered'], report['executed'], report['postgres_skipped']),
                         (1, 1, 0))
        self.assertNotIn('PARENT_PRIVATE_SENTINEL', (target / 'backend-tests.log').read_text())
        incorrect = {**manifest, 'expected_tests': 2}
        second = checker.extract_verified(self.root, incorrect, files)
        report = checker.run_backend(second, incorrect)
        self.assertEqual(report['status'], 'failed')
        self.assertFalse(report['counts_match'])

    def test_only_actual_opt_in_postgres_skips_are_counted_separately(self):
        self.write('backend/tests/test_synthetic_postgres.py', b'''import unittest
@unittest.skip('Opt-in disposable PostgreSQL fixture')
class PgTests(unittest.TestCase):
    def test_real_pg_is_not_executed(self):
        raise AssertionError('Must remain skipped in SQLite')
''')
        archive, result = self.build(commit=self.commit_files(), expected_tests=2)
        manifest, files = self.admitted(archive, result)
        target = checker.extract_verified(self.root, manifest, files)
        report = checker.run_backend(target, manifest)
        self.assertEqual(report['status'], 'passed')
        self.assertEqual((report['discovered'], report['executed'], report['postgres_skipped']),
                         (2, 1, 1))
        self.write('backend/tests/test_synthetic_postgres.py', b'''import unittest
@unittest.skip('Unexpected skip must fail release acceptance')
class PgTests(unittest.TestCase):
    def test_real_pg_is_not_executed(self):
        pass
''')
        second_archive, second_result = self.build(
            commit=self.commit_files(), name='unexpected-skip.tar', expected_tests=2,
        )
        manifest, files = self.admitted(second_archive, second_result)
        target = checker.extract_verified(self.root, manifest, files)
        report = checker.run_backend(target, manifest)
        self.assertEqual(report['status'], 'failed')
        self.assertFalse(report['counts_match'])

    def test_expected_failure_does_not_masquerade_as_release_success(self):
        self.write('backend/tests/test_sanity.py', b'''import unittest
class SanityTests(unittest.TestCase):
    @unittest.expectedFailure
    def test_known_failure(self):
        self.fail('A failure is not a completed acceptance check')
''')
        archive, result = self.build(commit=self.commit_files())
        manifest, files = self.admitted(archive, result)
        target = checker.extract_verified(self.root, manifest, files)
        report = checker.run_backend(target, manifest)
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['expected_failures'], 1)


if __name__ == '__main__':
    unittest.main()
