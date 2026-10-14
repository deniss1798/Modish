"""Offline regression for the ID-only import invariant; no .env or network."""
import importlib.util
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
with patch.dict(os.environ, {'DATABASE_URL': 'sqlite://', 'JWT_SECRET': 'isolated-fixture',
                            'APP_ENV': 'test', 'PYTHON_DOTENV_DISABLED': '1'}, clear=True), patch('dotenv.load_dotenv'):
    from sqlalchemy import create_engine, event, select
    from sqlalchemy.orm import Session
    from app.db import Base
    from app.models import Product
spec = importlib.util.spec_from_file_location('bounded', ROOT / 'scripts/release14_import_bounded.py')
bounded = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bounded)


class IdentitySnapshotTests(unittest.TestCase):
    def test_variants_and_shared_tokens_across_sources_are_not_hydrated(self):
        engine = create_engine('sqlite://')
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            db.add_all([Product(id=identifier, source=source, external_id=external,
                       title='fixture', brand='fixture', category='рубашки', price=1000,
                       image_url='https://fixture.invalid/' + identifier,
                       product_url='https://merchant.invalid/' + identifier,
                       feed_raw_json={'heavy': 'x' * 100000}) for identifier, source, external in
                      [('a', 'baon', 'same'), ('b', 'shoppinglive', 'same'),
                       ('c', 'shoppinglive', 'variant-1'), ('d', 'shoppinglive', 'variant-2'),
                       ('e', 'vipavenue', 'outside')]])
            db.commit()
        queries = []
        def observe(conn, cursor, statement, parameters, context, executemany):
            queries.append(statement.lower())
        event.listen(engine, 'before_cursor_execute', observe)
        with Session(engine) as db:
            result = bounded.identity_snapshot(db)
            self.assertEqual(result, {'a': ('baon', 'same'), 'b': ('shoppinglive', 'same'),
                                     'c': ('shoppinglive', 'variant-1'), 'd': ('shoppinglive', 'variant-2')})
            self.assertEqual(len(db.identity_map), 0)
            self.assertEqual(len(queries), 1)
            self.assertNotIn('feed_raw_json', queries[0])
            self.assertNotIn('available_sizes', queries[0])
        engine.dispose()

    def test_old_variant_id_reassignment_or_loss_fails_the_invariant(self):
        before = {'a': ('baon', 'same'), 'b': ('baon', 'same')}
        for after in ({'a': ('baon', 'same')},
                      {'a': ('baon', 'same'), 'b': ('shoppinglive', 'same')},
                      {'a': ('baon', 'changed'), 'b': ('baon', 'same')}):
            self.assertFalse(all(after.get(pid) == identity for pid, identity in before.items()))
        extended = {**before, 'new': ('baon', 'new')}
        self.assertTrue(all(extended.get(pid) == identity for pid, identity in before.items()))


if __name__ == '__main__':
    unittest.main()
