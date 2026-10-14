"""Offline release gates: live Compose provenance, evidence and snapshot honesty."""
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('activation', ROOT / 'scripts/release14_activation_remote.py')
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)


class ActivationTests(unittest.TestCase):
    def report(self):
        return json.loads((ROOT / 'docs/modish2/build14-bounded-report.json').read_text())

    def test_exact_completed_clone_evidence_and_each_missing_gate(self):
        report = self.report()
        a.evidence(report)
        for path, value in ((('photo_postgres', 'external_provider_calls'), 1),
                            (('photo_postgres', 'checks', 'onboarding_duplicate_key', 'provider_calls'), 2),
                            (('discovery_postgres', 'profile_count'), 9),
                            (('discovery_http', 'cold_strict_feed_status'), 200),
                            (('clone_import', 'existing_product_ids_preserved'), False),
                            (('outside_sources_unchanged',), False),
                            (('unit_tests', 'count'), 434)):
            with self.subTest(path=path):
                changed = copy.deepcopy(report)
                parent = changed
                for key in path[:-1]:
                    parent = parent[key]
                parent[path[-1]] = value
                with self.assertRaises(a.GuardError):
                    a.evidence(changed)

    def test_effective_old_configuration_uses_both_ordered_files(self):
        old = {'services': {'backend': {'image': a.BASE, 'environment': {'MODE': 'live-override'}},
                            'postgres': {'image': 'preserved'}}}
        new = copy.deepcopy(old)
        new['services']['backend'].update(image=a.IMAGE, entrypoint=['python', '-m', 'uvicorn'],
            command=['app.main:app', '--host', '0.0.0.0', '--port', '8000'], pull_policy='never', stop_grace_period='240s')
        calls = []

        def capture(*command):
            calls.append(command)
            return json.dumps(old if len(calls) % 2 else new).encode()

        target = Path('/isolated/candidate.compose.yml')
        with patch.object(a, 'capture', side_effect=capture):
            a.validate_compose_variant(target, a.IMAGE, 'app.main:app')
        self.assertEqual(calls[0], tuple(a.compose_command(a.LEGACY_COMPOSE_FILES) + ['config', '--format', 'json']))
        self.assertEqual(calls[1], tuple(a.compose_command(target) + ['config', '--format', 'json']))
        # A lost override value or dependency change must block the switch.
        for changed in (('backend', 'environment', {'MODE': 'base'}), ('postgres', 'image', 'changed')):
            with self.subTest(changed=changed):
                broken = copy.deepcopy(new)
                broken['services'][changed[0]][changed[1]] = changed[2]
                with patch.object(a, 'capture', side_effect=[json.dumps(old).encode(), json.dumps(broken).encode()]):
                    with self.assertRaisesRegex(a.GuardError, 'outside_backend_execution'):
                        a.validate_compose_variant(target, a.IMAGE, 'app.main:app')

    def test_backend_rewrite_drops_build_but_preserves_env_mounts_and_dependencies(self):
        original = 'services:\n  backend:\n    build:\n      context: ./backend\n    command: old\n    env_file: ./backend/.env\n    volumes:\n      - uploads:/app/uploads\n  postgres:\n    image: postgres:16\nvolumes:\n  uploads:\n'
        rewritten = a.backend_execution(original, a.IMAGE, 'app.main:app')
        self.assertNotIn('    build:', rewritten)
        self.assertNotIn('context:', rewritten)
        self.assertNotIn('command: old', rewritten)
        self.assertIn('env_file: ./backend/.env', rewritten)
        self.assertIn('uploads:/app/uploads', rewritten)
        self.assertIn('  postgres:\n    image: postgres:16\nvolumes:\n  uploads:\n', rewritten)

    def test_snapshot_age_and_observation_hashes_are_checked_without_writes(self):
        report = self.report()
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as directory, patch.object(a, 'R', Path(directory)):
            feeds = a.R / 'feeds'
            feeds.mkdir()
            manifest = {'result': 'download_complete', 'scope': 'four_fixed_admitad_https_exports_no_database',
                        'observation_clock_basis': 'real_download_request_start_utc', 'sources': []}
            for code, feed in a.FEEDS.items():
                payload = ('fixture-' + code).encode()
                (feeds / (code + '.csv')).write_bytes(payload)
                observed = (now - timedelta(hours=1)).isoformat()
                row = {'code': code, 'feed_id': feed, 'filename': code + '.csv',
                       'sha256': hashlib.sha256(payload).hexdigest(), 'bytes': len(payload),
                       'observed_at': observed, 'download_finished_at': now.isoformat()}
                manifest['sources'].append(row)
                matched = next(r for r in report['clone_import']['sources'] if r['code'] == code)
                matched.update(snapshot_sha256=row['sha256'], observed_at=observed)
            path = feeds / 'download-manifest.json'
            path.write_text(json.dumps(manifest))
            a.snapshots(report)
            manifest['sources'][0]['observed_at'] = (now - timedelta(hours=73)).isoformat()
            report['clone_import']['sources'][0]['observed_at'] = manifest['sources'][0]['observed_at']
            path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(a.GuardError, 'snapshots_expired'):
                a.snapshots(report)

    def test_activation_requires_user_import_approval_before_any_plan_or_db_read(self):
        class Args:
            production_import_approved = False
        with patch.object(a, 'load_plan') as loader, patch.object(a, 'production_scope') as scope:
            with self.assertRaisesRegex(a.GuardError, 'explicit_production_import_approval'):
                a.activate(Args())
            loader.assert_not_called()
            scope.assert_not_called()

    def test_live_compose_change_blocks_atomic_replacement(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(a, 'COMPOSE', Path(directory) / 'compose.yml'):
            a.COMPOSE.write_text('preserved')
            with self.assertRaisesRegex(a.GuardError, 'live_compose_changed'):
                a.atomic_compose('candidate', {'0' * 64})
            self.assertEqual(a.COMPOSE.read_text(), 'preserved')
            self.assertEqual(len(list(Path(directory).iterdir())), 1)

    def test_import_mutation_tail_matches_the_successful_isolated_import(self):
        before = (ROOT / 'scripts/release14_import_bounded.py').read_text()
        after = (ROOT / 'scripts/release14_import_production.py').read_text()
        marker = '            existing_ids = identity_snapshot(db)'
        self.assertEqual(before[before.index(marker):], after[after.index(marker):])
        for name in ('verified_snapshots', 'identity_snapshot', 'unseen_ids_predicate'):
            def body(source):
                start = source.index('\ndef ' + name + '(')
                finish = source.find('\ndef ', start + 1)
                return source[start:finish]
            self.assertEqual(body(before), body(after))


if __name__ == '__main__':
    unittest.main()
