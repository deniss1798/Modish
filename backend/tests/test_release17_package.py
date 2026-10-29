"""The next release admits one ledger migration and keeps the full old chain."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[2] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import package_release17 as package


def payload():
    files = {'backend/app/main.py': b"API_VERSION = '1.0.3-build17'\n",
        'backend/app/models.py': b'class CatalogRefreshRun(Base): pass\n',
        'backend/scripts/catalog_refresh17.py': b'# authored worker\n'}
    for suffix in ('download', 'ledger', 'runner', 'snapshot', 'writer'):
        files['backend/app/services/catalog/refresh_' + suffix + '.py'] = b'# authored module\n'
    revisions = [('20261013_0025', None), ('20261015_0026', '20261013_0025'),
        ('20261015_0027', '20261015_0026'), ('20261015_0028', '20261015_0027')]
    for revision, parent in revisions:
        files['backend/alembic/versions/' + revision + '.py'] = (
            'revision = ' + repr(revision) + '\ndown_revision = ' + repr(parent) + '\n').encode()
    files[package.MIGRATION_FILE] = b"revision = '20261028_0029'\ndown_revision = '20261015_0028'\n"
    return files


class Release17MetadataTests(unittest.TestCase):
    def validate(self, files):
        with patch.object(package.common, 'API_VERSION', package.API_VERSION):
            return package.validate_source_metadata(files)

    def test_complete_single_additive_revision_and_registered_worker(self):
        self.assertEqual(self.validate(payload()), 0)

    def test_parent_change_or_second_head_cannot_enter_package(self):
        files = payload()
        files[package.MIGRATION_FILE] = b"revision='20261028_0029'\ndown_revision='20261015_0027'\n"
        with self.assertRaisesRegex(package.common.PackageError, 'single_additive'):
            self.validate(files)
        files = payload()
        files['backend/alembic/versions/extra.py'] = b"revision='unreviewed'\ndown_revision='20261015_0028'\n"
        with self.assertRaisesRegex(package.common.PackageError, 'single_build15_migration_head'):
            self.validate(files)

    def test_missing_legacy_parent_is_still_rejected(self):
        files = payload()
        del files['backend/alembic/versions/20261015_0026.py']
        with self.assertRaisesRegex(package.common.PackageError, 'dangling_migration_parent'):
            self.validate(files)

    def test_unregistered_ledger_or_incomplete_worker_is_rejected(self):
        files = payload()
        files['backend/app/models.py'] = b'class OtherModel: pass\n'
        with self.assertRaisesRegex(package.common.PackageError, 'registered_refresh_ledger'):
            self.validate(files)
        files = payload()
        del files['backend/app/services/catalog/refresh_runner.py']
        with self.assertRaisesRegex(package.common.PackageError, 'complete_refresh_worker'):
            self.validate(files)


if __name__ == '__main__':
    unittest.main()
