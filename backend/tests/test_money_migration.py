from decimal import Decimal
import importlib.util
import io
from pathlib import Path
import unittest

from alembic.migration import MigrationContext
from alembic.operations import Operations
import sqlalchemy as sa


_PATH = Path(__file__).resolve().parents[1] / 'alembic/versions/20261015_0026_decimal_ruble_prices.py'
_SPEC = importlib.util.spec_from_file_location('decimal_ruble_migration', _PATH)
migration = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(migration)


class MoneyMigrationTests(unittest.TestCase):
    def setUp(self):
        self.engine = sa.create_engine('sqlite://')
        metadata = sa.MetaData()
        self.sources = sa.Table('product_sources', metadata, sa.Column('id', sa.String, primary_key=True))
        self.products = sa.Table('products', metadata,
            sa.Column('id', sa.String, primary_key=True),
            sa.Column('source_id', sa.String, sa.ForeignKey('product_sources.id'), index=True),
            sa.Column('title', sa.String), sa.Column('facts', sa.JSON),
            sa.Column('price', sa.Integer, nullable=False), sa.Column('old_price', sa.Integer, nullable=True))
        self.outfits = sa.Table('outfits', metadata, sa.Column('id', sa.String, primary_key=True),
            sa.Column('total_price', sa.Integer, nullable=False))
        self.saved = sa.Table('saved', metadata, sa.Column('id', sa.String, primary_key=True),
            sa.Column('product_id', sa.String, sa.ForeignKey('products.id')))
        metadata.create_all(self.engine)
        with self.engine.begin() as connection:
            connection.execute(self.sources.insert(), {'id': 'retained-source'})
            connection.execute(self.products.insert(), [
                {'id': 'retained-product', 'source_id': 'retained-source', 'title': 'Блузка',
                 'facts': {'size': 'M'}, 'price': 1299, 'old_price': 1499},
                {'id': 'nullable-old', 'source_id': 'retained-source', 'title': 'Рубашка',
                 'facts': {'size': 'L'}, 'price': 100, 'old_price': None},
            ])
            connection.execute(self.outfits.insert(), {'id': 'retained-outfit', 'total_price': 2700})
            connection.execute(self.saved.insert(), {'id': 'retained-save', 'product_id': 'retained-product'})

    def tearDown(self):
        self.engine.dispose()

    def _upgrade(self, connection):
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()

    def _downgrade(self, connection):
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()

    def _assert_retained(self, connection):
        rows = connection.execute(sa.text('SELECT id, source_id, title, facts FROM products ORDER BY id')).all()
        self.assertEqual(len(rows), 2)
        self.assertEqual({row.id for row in rows}, {'retained-product', 'nullable-old'})
        self.assertEqual({row.source_id for row in rows}, {'retained-source'})
        self.assertEqual(connection.scalar(sa.text('SELECT product_id FROM saved')), 'retained-product')
        self.assertEqual(connection.scalar(sa.text('SELECT id FROM outfits')), 'retained-outfit')
        self.assertEqual(connection.exec_driver_sql('PRAGMA foreign_key_check').all(), [])
        self.assertTrue(any('source_id' in index['column_names'] for index in sa.inspect(connection).get_indexes('products')))

    def test_upgrade_preserves_units_ids_links_nulls_and_nonprice_facts(self):
        with self.engine.begin() as connection:
            self._upgrade(connection)
            reflected = sa.Table('products', sa.MetaData(), autoload_with=connection)
            self.assertIsInstance(reflected.c.price.type, sa.Numeric)
            self.assertEqual((reflected.c.price.type.precision, reflected.c.price.type.scale), (14, 2))
            product = connection.execute(reflected.select().where(reflected.c.id == 'retained-product')).one()
            self.assertEqual(product.price, Decimal('1299.00'))
            self.assertEqual(product.old_price, Decimal('1499.00'))
            self.assertEqual(product.facts, {'size': 'M'})
            self.assertEqual(product.title, 'Блузка')
            self.assertIsNone(connection.scalar(sa.select(reflected.c.old_price).where(reflected.c.id == 'nullable-old')))
            self._assert_retained(connection)

    def test_kopecks_persist_and_downgrade_refuses_before_altering_any_table(self):
        with self.engine.begin() as connection:
            self._upgrade(connection)
            connection.execute(sa.text("UPDATE products SET price=1299.50, old_price=1499.99 WHERE id='retained-product'"))
            connection.execute(sa.text('UPDATE outfits SET total_price=2799.49'))
            with self.assertRaisesRegex(RuntimeError, 'precision or range'):
                self._downgrade(connection)
            self.assertIsInstance(sa.inspect(connection).get_columns('outfits')[1]['type'], sa.Numeric)
            self.assertEqual(connection.scalar(sa.text('SELECT price FROM products WHERE id=\'retained-product\'')), 1299.50)
            self._assert_retained(connection)

    def test_lossless_whole_ruble_downgrade_and_upgrade(self):
        with self.engine.begin() as connection:
            self._upgrade(connection)
            self._downgrade(connection)
            self.assertTrue(all(isinstance(c['type'], sa.Integer) for c in sa.inspect(connection).get_columns('products')
                                if c['name'] in {'price', 'old_price'}))
            self.assertEqual(connection.scalar(sa.text("SELECT price FROM products WHERE id='retained-product'")), 1299)
            self._assert_retained(connection)
            self._upgrade(connection)
            self._assert_retained(connection)

    def test_downgrade_refuses_integer_range_overflow(self):
        with self.engine.begin() as connection:
            self._upgrade(connection)
            connection.execute(sa.text('UPDATE outfits SET total_price=3000000000'))
            with self.assertRaisesRegex(RuntimeError, 'precision or range'):
                self._downgrade(connection)
            self.assertEqual(connection.scalar(sa.text('SELECT total_price FROM outfits')), 3000000000)
            self._assert_retained(connection)

    def test_sqlite_with_enforced_foreign_keys_fails_before_mutation(self):
        with self.engine.connect() as connection:
            connection.exec_driver_sql('PRAGMA foreign_keys=ON')
            connection.commit()
            with self.assertRaisesRegex(RuntimeError, 'foreign_keys=OFF'):
                self._upgrade(connection)
            self.assertTrue(all(isinstance(c['type'], sa.Integer) for c in sa.inspect(connection).get_columns('products')
                                if c['name'] in {'price', 'old_price'}))
            self._assert_retained(connection)

    def test_postgres_upgrade_sql_preserves_rubles_and_explicit_precision(self):
        output = io.StringIO()
        context = MigrationContext.configure(dialect_name='postgresql', opts={'as_sql': True, 'output_buffer': output})
        with Operations.context(context):
            migration.upgrade()
        sql = output.getvalue()
        self.assertEqual(sql.count('TYPE NUMERIC(14, 2)'), 3)
        for column in ('price', 'old_price', 'total_price'):
            self.assertIn(f'USING {column}::numeric(14,2)', sql)
        self.assertNotIn('* 100', sql)
        self.assertEqual(migration.down_revision, '20261013_0025')
