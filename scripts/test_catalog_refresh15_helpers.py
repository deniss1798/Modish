"""Offline guards for the isolated rehearsal helpers; no sockets or DB."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone

ROOT = Path(__file__).resolve().parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ImportGuards(unittest.TestCase):
    def setUp(self):
        self.module = load('catalog_refresh15_import')

    def test_missing_uuid_rejected_before_database_import(self):
        with patch.object(self.module, 'RUN_ID', ''), self.assertRaisesRegex(RuntimeError, 'uuid_refresh_run_required'):
            self.module.main()

    def test_production_url_rejected_before_application_connection(self):
        env = {'AUDIT_ARCHIVE_SHA256': self.module.EXPECTED_ARCHIVE,
               'DATABASE_URL': 'postgresql+psycopg://modish_user:unused@modish_postgres/modish'}
        with patch.object(self.module, 'RUN_ID', 'a' * 32), patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, 'exact_isolated_database_required'):
                self.module.main()

    def test_unreviewed_archive_rejected_before_database_import(self):
        with patch.object(self.module, 'RUN_ID', 'a' * 32), patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, 'reviewed_build15_archive_required'):
                self.module.main()

    def test_unknown_reason_text_never_exported(self):
        self.assertEqual(self.module.bounded_reasons({'missing_size': 2, 'arbitrary private text': 3}),
                         {'missing_size': 2, 'other_reason': 3})

    def snapshots(self, root, *, age=timedelta(), missing_last=False):
        now = datetime.now(timezone.utc)
        rows = []
        for code, feed_id in self.module.FEEDS.items():
            raw = b'id;name;available;price;param;currency\n1;Example;true;10;;RUB\n'
            (root / (code + '.csv')).write_bytes(raw)
            rows.append({'code': code, 'feed_id': feed_id, 'filename': code + '.csv',
                         'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
                         'observed_at': (now - age).isoformat(), 'download_finished_at': now.isoformat()})
        manifest = {'result': 'download_complete', 'scope': 'four_fixed_admitad_https_exports_no_database',
                    'observation_clock_basis': 'real_download_request_start_utc',
                    'run_id': 'a' * 32, 'sources': rows[:-1] if missing_last else rows}
        raw = json.dumps(manifest).encode()
        (root / 'download-manifest.json').write_bytes(raw)
        return {'REFRESH_RUN_ID': 'a' * 32, 'REFRESH_MANIFEST_SHA256': hashlib.sha256(raw).hexdigest()}

    def test_complete_current_snapshots_verified_offline(self):
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            env = self.snapshots(root)
            with patch.object(self.module, 'TARGET', root), patch.object(self.module, 'PREVIOUS_ROWS', dict.fromkeys(self.module.FEEDS, 2)), patch.dict(os.environ, env, clear=True):
                manifest, verified = self.module.verified_snapshots()
            self.assertEqual(set(verified), set(self.module.FEEDS))
            self.assertTrue(all(value['rows'] == 1 for value in verified.values()))

    def test_old_snapshot_cannot_be_renewed_by_processing(self):
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            env = self.snapshots(root, age=timedelta(hours=73))
            with patch.object(self.module, 'TARGET', root), patch.dict(os.environ, env, clear=True):
                with self.assertRaisesRegex(RuntimeError, 'current_observation_bound'):
                    self.module.verified_snapshots()

    def test_incomplete_source_set_rejected_before_any_write(self):
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            env = self.snapshots(root, missing_last=True)
            with patch.object(self.module, 'TARGET', root), patch.dict(os.environ, env, clear=True):
                with self.assertRaisesRegex(RuntimeError, 'exact_source_set_required'):
                    self.module.verified_snapshots()

    def test_changed_download_rejected(self):
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            env = self.snapshots(root)
            (root / 'baon.csv').write_bytes(b'replaced')
            with patch.object(self.module, 'TARGET', root), patch.dict(os.environ, env, clear=True):
                with self.assertRaisesRegex(RuntimeError, 'snapshot_hash_mismatch'):
                    self.module.verified_snapshots()

    def test_unpinned_manifest_rejected(self):
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            env = self.snapshots(root)
            env['REFRESH_MANIFEST_SHA256'] = '0' * 64
            with patch.object(self.module, 'TARGET', root), patch.dict(os.environ, env, clear=True):
                with self.assertRaisesRegex(RuntimeError, 'pinned_download_manifest_required'):
                    self.module.verified_snapshots()


class DownloadGuards(unittest.TestCase):
    def test_fixed_authorized_source_set(self):
        module = load('catalog_refresh15_download')
        self.assertEqual(module.FEEDS, {'baon': '19982', 'shoppinglive': '25461', 'serginnetti': '26417', 'postmeridiem': '25200'})
        self.assertEqual(module.MAX_BYTES, 256 * 1024 * 1024)


if __name__ == '__main__':
    unittest.main()
