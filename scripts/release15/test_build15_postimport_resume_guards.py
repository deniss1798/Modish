"""Offline contracts for the one-shot forward resume; no Docker/SSH/SQL."""
import ast
from contextlib import redirect_stdout
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

SOURCE = Path(__file__).with_name('build15_postimport_resume_remote.py')


def load():
    fake = ModuleType('fcntl')
    fake.flock = Mock(); fake.LOCK_EX, fake.LOCK_NB = 2, 4
    spec = importlib.util.spec_from_file_location('postimport_resume_offline', SOURCE)
    value = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'fcntl': fake}):
        spec.loader.exec_module(value)
    return value


class ResumeGuards(unittest.TestCase):
    def setUp(self):
        self.m = load()

    def test_import_is_inert_and_no_mutating_work_is_replayed(self):
        self.assertIsNone(self.m.BASE)
        self.assertFalse(self.m.STARTED)
        self.assertFalse(self.m.OPEN_ATTEMPTED)
        self.assertFalse(self.m.IMPORT_PROOF_VERIFIED)
        tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                self.assertNotIn(node.func.attr, {'execute_worker', 'commit_once', 'rollback_failure'})
                if isinstance(node.func.value, ast.Name) and node.func.value.id in {'CONT', 'contract'}:
                    self.assertNotEqual(node.func.attr, 'main')

    def failure(self):
        return {'operation': 'build15_approved_production_activation', 'status': 'failed',
            'phase': 'private_api_about_to_start', 'error_type': 'GuardError',
            'schema_migration_attempted': True, 'import_attempted': True,
            'import_committed_verified': True, 'import_outcome_uncertain_requires_review': False,
            'public_writes_may_exist': False, 'owned_cleanup_requires_review': False, 'automatic_retry': False}

    def commit(self):
        m = self.m
        return {'scope': 'build15_approved_four_source_production_import', 'result': 'production_import_committed',
            'plan_id': m.PLAN_ID, 'plan_sha256': m.PLAN_SHA, 'run_id': m.RUN, 'archive_sha256': m.ARCHIVE,
            'image_id': m.IMAGE, 'snapshot_manifest_sha256': m.MANIFEST, 'single_transaction': True,
            'transaction_state': 'committed', 'needs_review': False, 'existing_product_ids_preserved': True,
            'unaffected_rows_preserved': True, 'committed_sources': list(m.FEEDS),
            'sources': [{'code': code, 'rows': rows} for code, rows in m.ROWS.items()]}

    def test_exact_known_postimport_failure_is_accepted(self):
        self.m.valid_failure(self.failure())

    def test_any_unknown_commit_or_later_failure_is_rejected(self):
        for key, value in (('phase', 'public_container_create_about_to_start'),
                ('schema_migration_attempted', False), ('import_committed_verified', False),
                ('import_outcome_uncertain_requires_review', True), ('public_writes_may_exist', True),
                ('owned_cleanup_requires_review', True), ('automatic_retry', True), ('error_type', 'OtherError')):
            with self.subTest(key=key), self.assertRaisesRegex(RuntimeError, 'exact_known_postimport_smoke_failure_required'):
                self.m.valid_failure({**self.failure(), key: value})

    def test_exact_durable_four_source_commit_is_accepted(self):
        self.m.valid_commit(self.commit())

    def test_different_commit_identity_or_uncertain_outcome_is_rejected(self):
        for key, value in (('plan_id', '0' * 32), ('plan_sha256', '0' * 64), ('run_id', '0' * 32),
                ('archive_sha256', '0' * 64), ('image_id', 'sha256:' + '0' * 64),
                ('snapshot_manifest_sha256', '0' * 64), ('needs_review', True), ('single_transaction', False),
                ('transaction_state', 'commit_attempted'), ('existing_product_ids_preserved', False),
                ('unaffected_rows_preserved', False), ('committed_sources', ['baon'])):
            with self.subTest(key=key), self.assertRaisesRegex(RuntimeError, 'durable_exact_committed_import_required'):
                self.m.valid_commit({**self.commit(), key: value})

    def test_duplicate_missing_or_wrong_source_counts_rejected(self):
        for sources in ([{'code': 'baon', 'rows': 9318}] * 4,
                self.commit()['sources'][:-1], [{**row, 'rows': row['rows'] + 1} for row in self.commit()['sources']]):
            with self.subTest(sources=sources), self.assertRaisesRegex(RuntimeError, 'durable_exact_committed_import_required'):
                self.m.valid_commit({**self.commit(), 'sources': sources})

    def test_every_later_publication_marker_rejects_resume(self):
        for name in ('phase-99-writes_open_about_to_start.json', 'proxy-open-intent.json', 'activation-report.json',
                'phase-50-public_container_create_about_to_start.json', 'phase-51-loopback_api_about_to_start.json',
                'phase-49-private_smoke_passed.json', 'postimport-resume-writes_open-intent.json'):
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary); (root / name).write_text('{}')
                with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, 'later_publication_history_forbids_this_resume'):
                    self.m.closed_history(root)

    def test_nested_write_open_history_is_not_missed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); (root / 'nested').mkdir(); (root / 'nested/proxy-open-intent.json').write_text('{}')
            with self.assertRaisesRegex(RuntimeError, 'later_publication_history_forbids_this_resume'):
                self.m.closed_history(root)

    def test_failed_import_ledger_is_rejected_even_with_commit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); (root / 'production-import-outcome').mkdir()
            (root / 'production-import-outcome/failed.json').write_text('{}')
            with self.assertRaisesRegex(RuntimeError, 'failed_or_uncertain_import_forbids_resume'):
                self.m.closed_history(root)

    def test_known_closed_history_is_accepted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ('phase-10-private_api_about_to_start.json', 'activation-failed.json'):
                (root / name).write_text('{}')
            self.m.closed_history(root)

    def owned(self):
        m = self.m
        item = {'id': 'a' * 64, 'name': 'modish_postimport15_private_' + m.PLAN_ID,
            'helper_sha256': 'b' * 64, 'environment': {'DATABASE_URL': 'synthetic', 'PGOPTIONS': 'read-only'}}
        obj = {'Id': item['id'], 'Name': '/' + item['name'], 'Image': m.IMAGE, 'Mounts': [],
            'NetworkSettings': {'Networks': {'modish_default': {}}},
            'Config': {'Labels': {'org.modish.postimport_resume': m.PLAN_ID,
                'org.modish.postimport_helper_sha256': item['helper_sha256']},
                'Entrypoint': ['python'], 'Cmd': m.COMMAND[1:], 'Env': ['DATABASE_URL=synthetic', 'PGOPTIONS=read-only']},
            'HostConfig': {'NetworkMode': 'modish_default', 'PortBindings': {}, 'NanoCpus': 1000000000,
                'Memory': 1536 * 1024**2, 'MemorySwap': 1536 * 1024**2, 'PidsLimit': 128,
                'ReadonlyRootfs': True, 'Privileged': False, 'Devices': [], 'DeviceRequests': [],
                'PidMode': '', 'IpcMode': 'private', 'RestartPolicy': {'Name': 'no'},
                'CapDrop': ['ALL'], 'CapAdd': [], 'SecurityOpt': ['no-new-privileges']},
            'State': {'Running': False, 'ExitCode': 0, 'OOMKilled': False}}
        return obj, item

    def test_exact_private_worker_is_accepted(self):
        obj, item = self.owned(); self.assertIs(self.m.resume_owned(obj, item), obj)

    def test_private_worker_cannot_gain_ports_mounts_or_changed_environment(self):
        obj, item = self.owned()
        variants = []
        for key, value in (('Id', 'c' * 64), ('Image', 'sha256:' + 'c' * 64), ('Mounts', [{'RW': True}])):
            changed = deepcopy(obj); changed[key] = value; variants.append(changed)
        changed = deepcopy(obj); changed['HostConfig']['PortBindings'] = {'8000/tcp': [{'HostPort': '8000'}]}; variants.append(changed)
        changed = deepcopy(obj); changed['Config']['Env'].append('EXTERNAL_API_KEY=unexpected'); variants.append(changed)
        changed = deepcopy(obj); changed['Config']['Labels']['org.modish.activation_plan'] = self.m.PLAN_ID; variants.append(changed)
        for changed in variants:
            with self.subTest(changed=changed), self.assertRaisesRegex(RuntimeError, 'exact_owned_resume_private_api_required'):
                self.m.resume_owned(changed, item)

    def test_private_worker_resource_security_drift_is_rejected(self):
        original, item = self.owned()
        for key, value in (('NanoCpus', 0), ('Memory', 0), ('MemorySwap', -1), ('PidsLimit', 0),
                ('ReadonlyRootfs', False), ('Privileged', True), ('Devices', [{}]), ('DeviceRequests', [{}]),
                ('PidMode', 'host'), ('IpcMode', 'host'), ('RestartPolicy', {'Name': 'always'}),
                ('CapDrop', []), ('CapAdd', ['SYS_ADMIN']), ('SecurityOpt', [])):
            changed = deepcopy(original); changed['HostConfig'][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(RuntimeError, 'resume_private_resource_and_security_limits_required'):
                self.m.resume_owned(changed, item)

    def test_correct_smoke_contract_accepts_real_profile_route(self):
        routes = {'/health/ready': (200, {'version': '1.0.1-build15', 'status': 'ok', 'database': 'ok'}),
            '/catalog/status': (200, {'status': 'ready', 'personalized': False}),
            '/products/brands?limit=10': (200, [{'brand': 'Synthetic', 'count': 1}]),
            '/products/synthetic-sku': (200, {'id': 'synthetic-sku', 'price': 99.95}),
            '/feed?limit=1': (401, {'detail': 'Missing bearer token'}),
            '/catalog/discovery?limit=1': (401, {'detail': 'Missing bearer token'}),
            '/users/me': (401, {'detail': 'Missing bearer token'}),
            '/visual-analysis': (503, {'detail': 'Disabled'})}
        seen = []
        class Reply:
            def __init__(self, status, body): self.status, self.body = status, body
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, limit): return json.dumps(self.body).encode()[:limit]
        def opening(request, timeout):
            from urllib.parse import urlsplit
            parts = urlsplit(request.full_url); path = parts.path + ('?' + parts.query if parts.query else '')
            seen.append(path)
            return Reply(*routes.get(path, (404, {'detail': 'Not Found'})))
        output = io.StringIO()
        with patch('urllib.request.build_opener', return_value=SimpleNamespace(open=opening)), \
                patch.object(sys, 'argv', ['-', 'synthetic-sku', '99.95']), redirect_stdout(output):
            exec(compile(self.m.SMOKE, '<smoke-contract>', 'exec'), {})
        result = json.loads(output.getvalue())
        self.assertEqual(result['unauthenticated']['/users/me'], 401)
        self.assertNotIn('/profile', seen)
        self.assertTrue(result['real_sku_price_exact'])

    def test_frozen_recovery_refuses_resume_global_write_open_marker(self):
        recovery = SOURCE.with_name('build15_prewrite_restore_remote.py')
        fake = ModuleType('fcntl'); fake.flock = Mock(); fake.LOCK_EX, fake.LOCK_NB = 2, 4
        spec = importlib.util.spec_from_file_location('frozen_recovery_resume_contract', recovery)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'fcntl': fake}): spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); (root / 'postimport-resume-writes_open-intent.json').write_text('{}')
            with self.assertRaisesRegex(RuntimeError, 'public_writes_history_forbids_prewrite_restore'):
                module.closed_history(root)


if __name__ == '__main__':
    unittest.main()
