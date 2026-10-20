"""Create an immutable build15 payload from one complete Git commit, offline."""
from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile

from scan_secrets import findings


WORKSPACE = Path(__file__).resolve().parents[1]
BASE_IMAGE = 'sha256:eeea30209dcaa366fd936b6181ce84d47fc475a342df257893d58bb725dac1ec'
API_VERSION = '1.0.1-build15'
BASE_MIGRATION = '20261013_0025'
MIGRATION = '20261015_0028'
MANIFEST = 'source-manifest.json'
MAX_ARCHIVE = 256 * 1024 * 1024
MAX_MEMBER = 32 * 1024 * 1024
MAX_MANIFEST = 4 * 1024 * 1024
MAX_FILES = 10000
VERIFICATION_HELPERS = (
    'scripts/benchmark_activation_feedback.py',
    'scripts/check_backend.py',
    'scripts/run_device_fixture.py',
    'scripts/scan_secrets.py',
    'test/fixtures/build12-blouse.jpg',
)
EXCLUDED_PARTS = frozenset({
    '.git', '.venv', 'venv', '__pycache__', 'node_modules', '.tmp', 'build',
})
EXCLUDED_SUFFIXES = frozenset({
    '.pyc', '.pyo', '.dll', '.exe', '.so', '.dylib', '.class', '.db', '.sqlite',
    '.sqlite3', '.dump', '.pem', '.key', '.keystore', '.jks', '.zip', '.tar',
    '.gz', '.png', '.jpg', '.jpeg', '.webp', '.pdf',
})


class PackageError(RuntimeError):
    """Only fixed diagnostic codes; never source text or credentials."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise PackageError(code)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_name(name: str) -> bool:
    if not isinstance(name, str) or not name or len(name) > 240:
        return False
    path = PurePosixPath(name)
    if (path.is_absolute() or path.as_posix() != name or '\\' in name
            or any(ord(char) < 32 for char in name)):
        return False
    reserved = {'CON', 'PRN', 'AUX', 'NUL'} | {
        f'{prefix}{number}' for prefix in ('COM', 'LPT') for number in range(1, 10)
    }
    return all(
        part not in {'', '.', '..'} and ':' not in part
        and not part.endswith((' ', '.'))
        and part.split('.', 1)[0].upper() not in reserved
        for part in path.parts
    )


def excluded_backend(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(
        EXCLUDED_PARTS.intersection(path.parts)
        or (path.name.startswith('.env') and path.name != '.env.example')
        or path.suffix.lower() in EXCLUDED_SUFFIXES
    )


def allowed_payload(name: str) -> bool:
    return safe_name(name) and (
        name in VERIFICATION_HELPERS
        or (name.startswith('backend/') and not excluded_backend(name))
    )


def _literal_assignments(data: bytes, names: set[str]) -> dict:
    try:
        tree = ast.parse(data.decode('utf-8-sig'))
        values = {}
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in names:
                        require(target.id not in values, 'duplicate_metadata_assignment')
                        values[target.id] = ast.literal_eval(node.value)
        return values
    except (SyntaxError, UnicodeError, ValueError, TypeError):
        raise PackageError('invalid_ast_metadata') from None


def validate_source_metadata(files: dict[str, bytes]) -> int:
    require('backend/app/main.py' in files, 'api_main_missing')
    version = _literal_assignments(files['backend/app/main.py'], {'API_VERSION'})
    require(version.get('API_VERSION') == API_VERSION, 'build15_api_version_required')
    revisions = {}
    for name, data in files.items():
        if name.startswith('backend/alembic/versions/') and name.endswith('.py'):
            values = _literal_assignments(data, {'revision', 'down_revision'})
            revision, parent = values.get('revision'), values.get('down_revision')
            require(isinstance(revision, str) and revision not in revisions,
                    'missing_or_duplicate_migration_revision')
            require(parent is None or isinstance(parent, str)
                    or (isinstance(parent, tuple) and all(isinstance(p, str) for p in parent)),
                    'invalid_migration_parent')
            require('down_revision' in values, 'migration_parent_missing')
            revisions[revision] = parent
    parents = set()
    for revision in revisions:
        visiting, visited = set(), set()

        def visit(current):
            require(current in revisions, 'dangling_migration_parent')
            require(current not in visiting, 'migration_cycle')
            if current in visited:
                return
            visiting.add(current)
            parent = revisions[current]
            for item in parent if isinstance(parent, tuple) else (parent,):
                if item is not None:
                    parents.add(item)
                    visit(item)
            visiting.remove(current)
            visited.add(current)

        visit(revision)
    require(set(revisions) - parents == {MIGRATION}, 'single_build15_migration_head_required')
    require(revisions.get(MIGRATION) == '20261015_0027'
            and revisions.get('20261015_0027') == '20261015_0026'
            and revisions.get('20261015_0026') == BASE_MIGRATION,
            'build14_to_build15_chain_required')
    pg_methods = 0
    for name, data in files.items():
        if name.startswith('backend/tests/test_') and name.endswith('_postgres.py'):
            tree = ast.parse(data.decode('utf-8-sig'))
            pg_methods += sum(
                isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
                and method.name.startswith('test_')
                for cls in tree.body if isinstance(cls, ast.ClassDef)
                for method in cls.body
            )
    return pg_methods


def validate_payload(files: dict[str, bytes]) -> int:
    require(set(VERIFICATION_HELPERS).issubset(files), 'verification_helper_missing')
    require(all(allowed_payload(name) for name in files), 'nonallowlisted_payload')
    require(len({name.casefold() for name in files}) == len(files), 'case_colliding_payload')
    jpeg = files['test/fixtures/build12-blouse.jpg']
    require(jpeg.startswith(b'\xff\xd8\xff'), 'fixture_jpeg_required')
    # Archive validation never executes archived Python. The trusted local
    # release tool scans text; the committed guard is separately verified and
    # exercised by the backend tests in their isolated child process.
    for name, data in files.items():
        if name == 'test/fixtures/build12-blouse.jpg':
            continue
        try:
            text = data.decode('utf-8-sig')
        except UnicodeError:
            raise PackageError('binary_backend_payload_forbidden') from None
        require('\0' not in text, 'binary_backend_payload_forbidden')
        require(not any(findings(line, name) for line in text.splitlines()),
                'credential_guard_rejected_payload')
    return validate_source_metadata(files)


def _git(workspace: Path, *args: str, input: bytes | None = None) -> bytes:
    environment = dict(os.environ, GIT_NO_LAZY_FETCH='1', GIT_TERMINAL_PROMPT='0')
    result = subprocess.run(
        ['git', '--no-replace-objects', '-C', str(workspace),
         '-c', 'protocol.allow=never', *args], input=input, env=environment,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120,
    )
    require(result.returncode == 0, 'git_object_read_failed')
    return result.stdout


def committed_payload(workspace: Path, commit: str):
    require(bool(re.fullmatch(r'[0-9a-f]{40}', commit)), 'full_git_commit_required')
    actual = _git(workspace, 'rev-parse', '--verify', commit + '^{commit}').decode().strip()
    require(actual == commit, 'exact_git_commit_required')
    tree = _git(workspace, 'rev-parse', commit + '^{tree}').decode().strip()
    epoch = int(_git(workspace, 'show', '-s', '--format=%ct', commit).decode().strip())
    records, excluded = {}, []
    listing = _git(workspace, 'ls-tree', '-r', '-z', '--full-tree', commit,
                   '--', 'backend', *VERIFICATION_HELPERS)
    for raw in listing.split(b'\0'):
        if not raw:
            continue
        header, raw_name = raw.split(b'\t', 1)
        name = raw_name.decode('utf-8')
        mode, kind, oid = header.decode('ascii').split()
        require(safe_name(name), 'unsafe_git_path')
        if name.startswith('backend/') and excluded_backend(name):
            excluded.append(name)
            continue
        require(allowed_payload(name) and kind == 'blob' and mode in {'100644', '100755'},
                'regular_allowlisted_git_blob_required')
        require(name not in records, 'duplicate_git_path')
        records[name] = (oid, int(mode[-3:], 8))
    require(0 < len(records) <= MAX_FILES, 'git_payload_count_bound')
    # Read object IDs, never working-tree files; a concurrent checkout cannot
    # mix revisions. Batch mode also avoids one process per source file.
    object_ids = [oid for oid, _ in records.values()]
    batch_input = ('\n'.join(object_ids) + '\n').encode('ascii')
    headers = _git(workspace, 'cat-file', '--batch-check', input=batch_input).splitlines()
    require(len(headers) == len(object_ids), 'git_object_count_mismatch')
    sizes = []
    for expected, header in zip(object_ids, headers):
        parts = header.decode('ascii').split()
        require(len(parts) == 3 and parts[:2] == [expected, 'blob'],
                'git_object_type_mismatch')
        size = int(parts[2])
        require(0 <= size <= MAX_MEMBER, 'git_payload_size_bound')
        sizes.append(size)
    require(sum(sizes) <= MAX_ARCHIVE, 'git_payload_size_bound')
    raw_blobs = _git(workspace, 'cat-file', '--batch',
                     input=batch_input)
    files, modes, offset, total = {}, {}, 0, 0
    for name, (oid, mode) in records.items():
        end = raw_blobs.find(b'\n', offset)
        require(end >= 0, 'invalid_git_batch_header')
        header = raw_blobs[offset:end].decode('ascii').split()
        require(len(header) == 3 and header[:2] == [oid, 'blob'], 'git_batch_object_mismatch')
        size = int(header[2])
        total += size
        require(0 <= size <= MAX_MEMBER and total <= MAX_ARCHIVE, 'git_payload_size_bound')
        start, offset = end + 1, end + 2 + size
        require(raw_blobs[offset - 1:offset] == b'\n', 'invalid_git_batch_body')
        files[name], modes[name] = raw_blobs[start:start + size], mode
    require(offset == len(raw_blobs), 'trailing_git_batch_data')
    return files, modes, sorted(excluded), tree, epoch


def build_package(workspace: Path, commit: str, output: Path, *, expected_tests: int) -> dict:
    require(type(expected_tests) is int and 0 < expected_tests <= 100000, 'test_count_bound')
    files, modes, excluded, tree, epoch = committed_payload(workspace, commit)
    pg_methods = validate_payload(files)
    require(pg_methods <= expected_tests, 'postgres_count_exceeds_total')
    manifest = {
        'schema_version': 1, 'release': 'build15', 'commit': commit, 'tree': tree,
        'base_image': BASE_IMAGE, 'base_migration': BASE_MIGRATION, 'migration': MIGRATION,
        'api_version': API_VERSION, 'commit_epoch': epoch, 'expected_tests': expected_tests,
        'postgres_test_methods': pg_methods, 'verification_helpers': list(VERIFICATION_HELPERS),
        'excluded_backend_files': excluded,
        'files': {name: sha256(data) for name, data in sorted(files.items())},
        'modes': dict(sorted(modes.items())),
    }
    encoded = (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode('utf-8')
    require(len(encoded) <= MAX_MANIFEST, 'manifest_size_bound')
    # Exclusive creation is intentional: no existing artifact is overwritten,
    # including a failed partial build, which stays available for diagnosis.
    with output.open('xb') as destination:
        with tarfile.open(fileobj=destination, mode='w', format=tarfile.USTAR_FORMAT) as archive:
            for name, data in sorted({**files, MANIFEST: encoded}.items()):
                member = tarfile.TarInfo(name)
                member.size = len(data)
                member.mode = modes.get(name, 0o644)
                member.mtime = epoch
                archive.addfile(member, io.BytesIO(data))
    require(output.stat().st_size <= MAX_ARCHIVE, 'archive_size_bound')
    with output.open('rb') as source:
        archive_sha256 = hashlib.file_digest(source, 'sha256').hexdigest()
    return {
        'status': 'immutable_package_created', 'commit': commit,
        'archive_sha256': archive_sha256, 'files': len(files),
        'api_version': API_VERSION, 'migration': MIGRATION,
        'expected_tests': expected_tests, 'postgres_test_methods': pg_methods,
        'production_changed': False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit', required=True, help='Complete lowercase40hex commit ID')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--expected-tests', type=int, required=True)
    args = parser.parse_args()
    temporary = (WORKSPACE / '.tmp').resolve()
    output = args.output.resolve()
    require(temporary.is_relative_to(WORKSPACE) and output.is_relative_to(temporary),
            'workspace_tmp_output_required')
    require(output.parent.is_dir() and not output.is_symlink(), 'regular_new_output_required')
    print(json.dumps(build_package(WORKSPACE, args.commit, output,
                                   expected_tests=args.expected_tests)))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except PackageError as error:
        print(json.dumps({'status': 'rejected', 'guard': str(error)}))
        raise SystemExit(1)
