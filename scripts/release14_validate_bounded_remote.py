"""Retry the rolled-back isolated import with an ID-only memory fix.

Uses the existing private clone and unchanged snapshots. Never restores a DB,
changes production or raises container memory limits. Prior evidence is retained.
"""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path

R = Path('/opt/modish-releases/20261014-build14')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare-sha256', required=True)
    parser.add_argument('--import-sha256', required=True)
    args = parser.parse_args()
    if not __debug__:
        raise ValueError('python_optimization_forbidden')
    os.umask(0o077)
    spec = importlib.util.spec_from_file_location('prepare', R / 'release14_prepare_remote.py')
    p = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(p)
    p.require(R.resolve() == R and not R.is_symlink()
              and p.digest(R / 'release14_prepare_remote.py') == args.prepare_sha256, 'pinned_preparer_required')
    failed = json.loads((R / 'resume-failure-report.json').read_text())
    p.require(failed['phase'] == 'isolated_clone_catalogue_import'
              and failed['photo_postgres']['status'] == 'passed', 'preserved_failed_import_required')
    p.require(p.production_snapshot() == failed['production_before'], 'production_changed')
    p.require(p.inspect(p.PG)['Id'] == failed['test_pg_container_id'], 'test_postgres_changed')
    pg = p.inspect(p.PG)
    network = json.loads(p.capture('docker', 'network', 'inspect', p.NETWORK))[0]
    p.require(network['Internal'] and set(pg['NetworkSettings']['Networks']) == {p.NETWORK}
              and not pg['HostConfig'].get('PortBindings'), 'internal_test_network_required')
    p.require(p.query('SELECT version_num FROM alembic_version') == p.REVISION, 'clone_revision_changed')
    p.require(p.query('SELECT version_num FROM alembic_version', production=True) == p.OLD_REVISION, 'production_revision_changed')
    # The OOM happened before any successful raw-record transaction. Never
    # blindly retry if some catalogue writes might have committed.
    p.require(p.query('SELECT count(*) FROM catalog_records') == '0', 'prior_import_not_rolled_back')
    started = datetime.fromisoformat(failed['resume_started_at']).isoformat()
    p.require(p.query("SELECT count(*) FROM catalog_sync_runs WHERE started_at >= TIMESTAMPTZ '" + started + "'") == '0',
              'prior_sync_transaction_not_rolled_back')
    p.require(not (R / 'bounded-report.json').exists() and not (R / 'bounded-failure-report.json').exists(),
              'bounded_validation_already_started')
    for name, sha in failed['corrected_helpers'].items():
        p.require(p.digest(R / name) == sha, 'corrected_audit_helper_changed')
    p.require(p.digest(R / 'release14_import_bounded.py') == args.import_sha256, 'bounded_import_hash_mismatch')
    image = failed['image_id']
    p.require(p.inspect(image)['Id'] == image and p.digest(R / 'source.tar') == failed['archive_sha256'], 'candidate_changed')
    envfile = R / 'private/test-api.env'
    manifest = json.loads((R / 'feeds/download-manifest.json').read_text())
    for row in manifest['sources']:
        p.require(p.digest(R / 'feeds' / row['filename']) == row['sha256'], 'snapshot_changed')
    user_count = p.query('SELECT count(*) FROM users')
    outside = p.outside_sources_fingerprint()
    report = {**failed, 'status': 'validating_bounded_import', 'original_import_failure': 'resume-failure-report.json',
              'bounded_import_sha256': args.import_sha256, 'bounded_started_at': datetime.now(timezone.utc).isoformat()}
    for key in ('error_type', 'finished_at'):
        report.pop(key, None)
    try:
        p.progress(report, 'bounded_isolated_catalogue_import')
        imported = json.loads(p.clone_container(image, envfile, ['/audit.py'], name='bounded-import', timeout=7200,
          mounts=((R / 'release14_import_bounded.py', '/audit.py'), (R / 'feeds', '/feeds')),
          env=('REFRESH_RUN_ID=' + manifest['run_id'], 'AUDIT_IMAGE_ID=' + image,
               'AUDIT_ARCHIVE_SHA256=' + report['archive_sha256'])).read_text())
        p.require(imported['result'] == 'import_committed' and imported['single_transaction']
                  and imported['existing_product_ids_preserved'] and not imported['raw_timestamps_injected']
                  and set(imported['committed_sources']) == set(p.FEEDS), 'bounded_import_acceptance_failed')
        report['clone_import'] = imported
        p.require(p.outside_sources_fingerprint() == outside, 'outside_sources_changed')
        p.progress(report, 'ten_profile_catalogue_and_discovery_services')
        discovery = json.loads(p.clone_container(image, envfile,
          ['/audit.py', '--isolated-write-test', '--expected-host', p.PG, '--expected-database', p.DATABASE],
          name='discovery-bounded', mounts=((R / 'check_discovery_postgres_fixed.py', '/audit.py'),)).read_text())
        p.require(discovery['status'] == 'passed' and discovery['profile_count'] == 10
                  and discovery['fixture_cleanup'] == 'rolled_back' and discovery['users_restored'], 'discovery_acceptance_failed')
        report['discovery_postgres'] = discovery
        p.progress(report, 'authenticated_discovery_http_smoke')
        http = json.loads(p.clone_container(image, envfile,
          ['/audit.py', '--isolated-write-test', '--expected-host', p.PG, '--expected-database', p.DATABASE],
          name='discovery-http-bounded', mounts=((R / 'check_discovery_http_postgres.py', '/audit.py'),)).read_text())
        p.require(http['status'] == 'passed' and http['fixture_cleanup'] == 'committed'
                  and http['external_provider_calls'] == 0 and http['cold_strict_feed_status'] == 409,
                  'discovery_http_acceptance_failed')
        report['discovery_http'] = http
        p.require(p.query('SELECT count(*) FROM users') == user_count and p.outside_sources_fingerprint() == outside,
                  'fixture_or_outside_state_changed')
        p.require(p.production_snapshot() == failed['production_before'], 'production_metadata_changed')
        report.update(status='prepared_ready_for_review', phase='isolated_checks_complete',
                      outside_sources_unchanged=True, production_metadata_unchanged=True,
                      production_catalogue_imported=False, production_database_writes=False, ready_for_activation=False,
                      pending_checks=['production_import_explicit_approval', 'reviewed_activation_and_application_rollback'],
                      catalogue_full_outfit_readiness='unproven', finished_at=datetime.now(timezone.utc).isoformat())
        p.write_json(R / 'bounded-report.json', report)
        print(json.dumps({'status': report['status'], 'ready_for_activation': False,
                          'photo_checks': 5, 'profiles': 10, 'http_smoke': 'passed'}))
        return 0
    except Exception as error:
        report.update(status='failed', error_type=type(error).__name__, finished_at=datetime.now(timezone.utc).isoformat())
        if hasattr(error, 'returncode'):
            report['process_exit_code'] = error.returncode
        if isinstance(error, p.GuardError):
            report['guard'] = str(error)
        p.write_json(R / 'bounded-failure-report.json', report)
        print(json.dumps({key: report[key] for key in ('status', 'phase', 'error_type', 'guard', 'process_exit_code') if key in report}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
