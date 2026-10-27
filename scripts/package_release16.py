"""Build16 immutable application payload; schema remains at the deployed0028."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import tarfile
from unittest.mock import patch

import package_release15 as common

API_VERSION = '1.0.2-build16'
DEPLOYED_IMAGE = 'sha256:633bce227d3ce3277137c77f32f2c6e83bb338d91784803df424d7592a464aec'
SCHEMA = '20261015_0028'


def build_package(workspace: Path, commit: str, output: Path, *, expected_tests: int):
    common.require(type(expected_tests) is int and 0 < expected_tests <= 100000, 'test_count_bound')
    files, modes, excluded, tree, epoch = common.committed_payload(workspace, commit)
    # Reuse the audited path/credential/complete migration-history validators.
    # Only the application version changes; do not edit the frozen build15 tool.
    with patch.object(common, 'API_VERSION', API_VERSION):
        pg_methods = common.validate_payload(files)
    common.require(pg_methods <= expected_tests, 'postgres_count_exceeds_total')
    manifest = {
        'schema_version': 1, 'release': 'build16', 'commit': commit, 'tree': tree,
        'base_image': DEPLOYED_IMAGE, 'base_migration': SCHEMA, 'migration': SCHEMA,
        'api_version': API_VERSION, 'commit_epoch': epoch, 'expected_tests': expected_tests,
        'postgres_test_methods': pg_methods, 'verification_helpers': list(common.VERIFICATION_HELPERS),
        'excluded_backend_files': excluded,
        'files': {name: common.sha256(data) for name, data in sorted(files.items())},
        'modes': dict(sorted(modes.items())),
    }
    encoded = (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode()
    common.require(len(encoded) <= common.MAX_MANIFEST, 'manifest_size_bound')
    with output.open('xb') as destination:
        with tarfile.open(fileobj=destination, mode='w', format=tarfile.USTAR_FORMAT) as archive:
            for name, data in sorted({**files, common.MANIFEST: encoded}.items()):
                member = tarfile.TarInfo(name)
                member.size, member.mode, member.mtime = len(data), modes.get(name, 0o644), epoch
                archive.addfile(member, io.BytesIO(data))
    common.require(output.stat().st_size <= common.MAX_ARCHIVE, 'archive_size_bound')
    with output.open('rb') as source:
        digest = hashlib.file_digest(source, 'sha256').hexdigest()
    return {'status': 'immutable_package_created', 'release': 'build16', 'commit': commit,
        'archive_sha256': digest, 'files': len(files), 'api_version': API_VERSION,
        'migration': SCHEMA, 'schema_change': False, 'expected_tests': expected_tests,
        'postgres_test_methods': pg_methods, 'production_changed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--expected-tests', type=int, required=True)
    args = parser.parse_args()
    root = common.WORKSPACE
    output = args.output.resolve()
    common.require(output.is_relative_to((root / '.tmp').resolve()) and output.parent.is_dir()
                   and not output.is_symlink(), 'new_workspace_artifact_required')
    print(json.dumps(build_package(root, args.commit, output, expected_tests=args.expected_tests)))


if __name__ == '__main__':
    try:
        main()
    except common.PackageError as error:
        print(json.dumps({'status': 'rejected', 'guard': str(error)}))
        raise SystemExit(1) from None
