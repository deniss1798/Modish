"""Continue the preserved build14 clone after correcting mounted audit paths.

No production mutation or repeated database restore. Previous failed evidence,
source image and dump stay intact; corrected helpers have separate pinned names.
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
    for name in ('prepare', 'photo', 'discovery', 'http'):
        parser.add_argument('--' + name + '-sha256', required=True)
    args = parser.parse_args()
    os.umask(0o077)
    if not __debug__:
        raise ValueError('python_optimization_forbidden')
    assert R.resolve() == R and not R.is_symlink()
    spec = importlib.util.spec_from_file_location('prepare', R / 'release14_prepare_remote.py')
    p = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(p)
    p.require(p.digest(R / 'release14_prepare_remote.py') == args.prepare_sha256, 'pinned_original_preparer_required')
    failed = json.loads((R / 'failure-report.json').read_text())
    p.require(failed['phase'] == 'postgres_photo_and_registration_concurrency'
              and failed['production_metadata_unchanged'] and not failed['production_database_writes'], 'preserved_failure_scope')
    p.require(p.production_snapshot() == failed['production_before'], 'production_metadata_changed')
    p.require(p.inspect(p.PG)['Id'] == failed['test_pg_container_id'], 'test_postgres_identity_changed')
    network = json.loads(p.capture('docker', 'network', 'inspect', p.NETWORK))[0]
    pg = p.inspect(p.PG)
    p.require(network['Internal'] and set(pg['NetworkSettings']['Networks']) == {p.NETWORK}
              and not pg['HostConfig'].get('PortBindings'), 'isolated_network_required')
    p.require(p.query('SELECT version_num FROM alembic_version') == p.REVISION, 'existing_clone_revision')
    p.require(p.query('SELECT version_num FROM alembic_version', production=True) == p.OLD_REVISION, 'production_revision_changed')
    image = failed['image_id']
    p.require(p.inspect(image)['Id'] == image and p.digest(R / 'source.tar') == failed['archive_sha256'], 'original_candidate_changed')
    for name, sha in failed['helpers'].items():
        p.require(p.digest(R / name) == sha, 'original_helper_changed')
    fixes = {'check_photo_postgres_fixed.py': args.photo_sha256,
             'check_discovery_postgres_fixed.py': args.discovery_sha256,
             'check_discovery_http_postgres.py': args.http_sha256}
    for name, sha in fixes.items():
        p.require((R / name).is_file() and not (R / name).is_symlink()
                  and p.digest(R / name) == sha, 'corrected_helper_hash_mismatch')
    p.require(not any((R / name).exists() for name in ('resume-report.json', 'resume-failure-report.json', 'feeds')),
              'continuation_already_started')
    report = {**failed, 'status': 'resuming_isolated_validation', 'original_failure': 'failure-report.json',
              'corrected_helpers': fixes, 'resume_started_at': datetime.now(timezone.utc).isoformat(),
              'ready_for_activation': False}
    for key in ('error_type', 'finished_at'):
        report.pop(key, None)
    envfile = R / 'private/test-api.env'
    user_count = p.query('SELECT count(*) FROM users')
    try:
        p.progress(report, 'corrected_postgres_photo_and_registration_concurrency')
        photo = json.loads(p.clone_container(image, envfile,
          ['/audit.py', '--isolated-write-test', '--expected-host', p.PG, '--expected-database', p.DATABASE],
          name='photo-fixed', mounts=((R / 'check_photo_postgres_fixed.py', '/audit.py'),)).read_text())
        p.require(photo['status'] == 'passed' and photo['fixture_cleanup'] == 'committed'
                  and len(photo['checks']) == 5 and photo['external_provider_calls'] == 0,
                  'corrected_photo_acceptance_failed')
        report['photo_postgres'] = photo
        p.require(p.query('SELECT count(*) FROM users') == user_count, 'photo_fixture_cleanup_failed')
        p.progress(report, 'copy_existing_fixed_snapshots')
        refresh, report['feed_snapshots'] = p.copy_snapshots()
        outside = p.outside_sources_fingerprint()
        p.progress(report, 'isolated_clone_catalogue_import')
        imported = json.loads(p.clone_container(image, envfile, ['/audit.py'], name='clone-import', timeout=7200,
          mounts=((R / 'release14_import_clone.py', '/audit.py'), (R / 'feeds', '/feeds')),
          env=('REFRESH_RUN_ID=' + refresh, 'AUDIT_IMAGE_ID=' + image,
               'AUDIT_ARCHIVE_SHA256=' + report['archive_sha256'])).read_text())
        p.require(imported['result'] == 'import_committed' and imported['single_transaction']
                  and imported['existing_product_ids_preserved'] and not imported['raw_timestamps_injected']
                  and set(imported['committed_sources']) == set(p.FEEDS), 'clone_import_acceptance_failed')
        report['clone_import'] = imported
        p.require(p.outside_sources_fingerprint() == outside, 'outside_sources_changed')
        p.progress(report, 'ten_profile_catalogue_and_discovery_services')
        discovery = json.loads(p.clone_container(image, envfile,
          ['/audit.py', '--isolated-write-test', '--expected-host', p.PG, '--expected-database', p.DATABASE],
          name='discovery-fixed', mounts=((R / 'check_discovery_postgres_fixed.py', '/audit.py'),)).read_text())
        p.require(discovery['status'] == 'passed' and discovery['profile_count'] == 10
                  and discovery['fixture_cleanup'] == 'rolled_back' and discovery['users_restored'], 'discovery_acceptance_failed')
        report['discovery_postgres'] = discovery
        p.progress(report, 'authenticated_discovery_http_smoke')
        http = json.loads(p.clone_container(image, envfile,
          ['/audit.py', '--isolated-write-test', '--expected-host', p.PG, '--expected-database', p.DATABASE],
          name='discovery-http', mounts=((R / 'check_discovery_http_postgres.py', '/audit.py'),)).read_text())
        p.require(http['status'] == 'passed' and http['fixture_cleanup'] == 'committed'
                  and http['external_provider_calls'] == 0 and http['cold_strict_feed_status'] == 409,
                  'discovery_http_acceptance_failed')
        report['discovery_http'] = http
        p.require(p.query('SELECT count(*) FROM users') == user_count and p.outside_sources_fingerprint() == outside,
                  'fixture_or_outside_source_state_changed')
        p.require(p.production_snapshot() == failed['production_before'], 'production_metadata_changed')
        for name, sha in fixes.items():
            p.require(p.digest(R / name) == sha, 'corrected_helper_changed')
        report.update(status='prepared_ready_for_review', phase='isolated_checks_complete',
                      outside_sources_unchanged=True, production_metadata_unchanged=True,
                      production_catalogue_imported=False, production_database_writes=False,
                      pending_checks=['production_import_explicit_approval', 'reviewed_activation_and_application_rollback'],
                      catalogue_full_outfit_readiness='unproven', finished_at=datetime.now(timezone.utc).isoformat())
        p.write_json(R / 'resume-report.json', report)
        print(json.dumps({'status': report['status'], 'ready_for_activation': False,
                          'photo_checks': 5, 'profiles': 10, 'http_smoke': 'passed'}))
        return 0
    except Exception as error:
        report.update(status='failed', error_type=type(error).__name__, finished_at=datetime.now(timezone.utc).isoformat())
        if isinstance(error, p.GuardError):
            report['guard'] = str(error)
        p.write_json(R / 'resume-failure-report.json', report)
        print(json.dumps({key: report[key] for key in ('status', 'phase', 'error_type', 'guard') if key in report}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
