"""Immutable backend17 payload with the single additive refresh ledger migration."""
from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
from pathlib import Path
import tarfile
from unittest.mock import patch

import package_release15 as common

API_VERSION = '1.0.3-build17'
DEPLOYED_IMAGE = 'sha256:61d94e0ab68c7a9342b1676a9179ea554e7e5940bf5e67622d783a8767b02a3a'
BASE_SCHEMA = '20261015_0028'
SCHEMA = '20261028_0029'
MIGRATION_FILE = 'backend/alembic/versions/20261028_0029_catalog_refresh_runs.py'
VERIFICATION_HELPERS = (*common.VERIFICATION_HELPERS,
    'scripts/package_release15.py', 'scripts/package_release17.py',
    'scripts/catalog_refresh17_host.py',
    'deploy/systemd/modish-catalog-refresh.service',
    'deploy/systemd/modish-catalog-refresh.timer')
_legacy_source_validator = common.validate_source_metadata


def validate_source_metadata(files):
    """Keep the audited complete history check, then admit exactly migration29."""
    common.require(MIGRATION_FILE in files, 'refresh_ledger_migration_required')
    migration = common._literal_assignments(files[MIGRATION_FILE], {'revision', 'down_revision'})
    common.require(migration == {'revision': SCHEMA, 'down_revision': BASE_SCHEMA},
        'single_additive_refresh_ledger_revision_required')
    legacy = {name: data for name, data in files.items() if name != MIGRATION_FILE}
    pg_methods = _legacy_source_validator(legacy)
    models = ast.parse(files['backend/app/models.py'].decode('utf-8-sig'))
    common.require(any(isinstance(node, ast.ClassDef) and node.name == 'CatalogRefreshRun'
        for node in models.body), 'registered_refresh_ledger_model_required')
    common.require('backend/scripts/catalog_refresh17.py' in files
        and all('backend/app/services/catalog/refresh_' + name + '.py' in files
            for name in ('download', 'ledger', 'runner', 'snapshot', 'writer')),
        'complete_refresh_worker_payload_required')
    return pg_methods


def build_package(workspace: Path, commit: str, output: Path, *, expected_tests: int):
    common.require(type(expected_tests) is int and 0 < expected_tests <= 100000, 'test_count_bound')
    with patch.object(common, 'VERIFICATION_HELPERS', VERIFICATION_HELPERS):
        files, modes, excluded, tree, epoch = common.committed_payload(workspace, commit)
    with patch.object(common, 'VERIFICATION_HELPERS', VERIFICATION_HELPERS), \
            patch.object(common, 'API_VERSION', API_VERSION), patch.object(
            common, 'validate_source_metadata', validate_source_metadata):
        pg_methods = common.validate_payload(files)
    common.require(pg_methods <= expected_tests, 'postgres_count_exceeds_total')
    manifest = {
        'schema_version': 1, 'release': 'build17', 'commit': commit, 'tree': tree,
        'base_image': DEPLOYED_IMAGE, 'base_migration': BASE_SCHEMA, 'migration': SCHEMA,
        'api_version': API_VERSION, 'commit_epoch': epoch, 'expected_tests': expected_tests,
        'postgres_test_methods': pg_methods, 'verification_helpers': list(VERIFICATION_HELPERS),
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
    with output.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'status': 'immutable_package_created', 'release': 'build17', 'commit': commit,
        'archive_sha256': digest, 'files': len(files), 'api_version': API_VERSION,
        'migration': SCHEMA, 'base_migration': BASE_SCHEMA, 'schema_change': True,
        'expected_tests': expected_tests, 'postgres_test_methods': pg_methods,
        'production_changed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--expected-tests', type=int, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    common.require(output.is_relative_to((common.WORKSPACE / '.tmp').resolve())
        and output.parent.is_dir() and not output.is_symlink(), 'new_workspace_artifact_required')
    print(json.dumps(build_package(common.WORKSPACE, args.commit, output,
        expected_tests=args.expected_tests)))


if __name__ == '__main__':
    try:
        main()
    except common.PackageError as error:
        print(json.dumps({'status': 'rejected', 'guard': str(error)}))
        raise SystemExit(1) from None
