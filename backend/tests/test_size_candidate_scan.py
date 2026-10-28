"""Thin retrieval preserves exact admission, order and ORM session semantics."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, event, func, insert, select, update
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Product
from app.services.catalog import candidate_scan
from app.services.catalog.candidate_scan import iter_size_candidates
from app.services.catalog.size_resolver import exact_size_match
from app.services.fit_size_profile import requested_size_for_product


class SizeCandidateScanTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, autoflush=False)()
        self.fit = self.profile()
        self.query = select(Product).where(Product.is_active == 1).order_by(Product.id)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def profile(self, **choices):
        selected = {'top': ('M', 'LETTER'), 'bottom': ('M', 'LETTER'),
                    'one_piece': ('M', 'LETTER'), 'shoes': ('39', 'EU')}
        selected.update(choices)
        return SimpleNamespace(size_profile_json={'sizes': {
            slot: {'value': value, 'system': system, 'state': 'confirmed'}
            for slot, (value, system) in selected.items()}})

    def row(self, pid, *, title='Рубашка однотонная', category='рубашки', sizes=None, **fields):
        result = dict(id=pid, external_id=pid, source='scan-test', brand='Test',
            title=title, category=category, category_name=category,
            available_sizes=['M'] if sizes is None else sizes, price=1500,
            image_url='https://example.test/' + pid + '.jpg',
            product_url='https://example.test/' + pid, is_active=1,
            feed_raw_json={'size_system': 'LETTER'},
            description='Merchant blob ' * 1000)
        result.update(fields)
        return result

    def seed(self, rows):
        self.db.execute(insert(Product), rows)
        self.db.commit()

    def ids(self, query=None, *, fit=None, batch_size=200):
        return [product.id for product in iter_size_candidates(self.db,
            self.query if query is None else query, self.fit if fit is None else fit,
            batch_size=batch_size)]

    def admitted_ids(self, products):
        result = []
        for product in products:
            wanted, system = requested_size_for_product(self.fit, product)
            if exact_size_match(product, wanted, requested_system=system):
                result.append(product.id)
        return result

    def test_wrong_sizes_do_not_hydrate_blobs_and_late_winner_is_not_capped(self):
        self.seed([self.row(f'{i:05}', sizes=['S']) for i in range(1501)] +
                  [self.row('00010-match'), self.row('99999-late')])
        loaded = []

        def on_load(product, context):
            loaded.append(product.id)

        event.listen(Product, 'load', on_load)
        try:
            self.assertEqual(self.ids(), ['00010-match', '99999-late'])
        finally:
            event.remove(Product, 'load', on_load)
        self.assertEqual(loaded, ['00010-match', '99999-late'])

    def test_outer_join_order_and_batch_sizes_match_full_reference(self):
        self.seed([self.row(f'{i:03}', sizes=['S'] if i % 4 == 0 else ['M'],
            price=1000 + i % 7) for i in range(233)])
        popularity = select(Product.id.label('pid'), Product.price.label('n')).where(
            Product.price > 1002).subquery()
        query = select(Product).outerjoin(popularity, Product.id == popularity.c.pid).where(
            Product.is_active == 1, Product.price < 1006).order_by(
            func.coalesce(popularity.c.n, 0).desc(), Product.id.desc())
        expected = self.admitted_ids(self.db.scalars(query))
        self.db.expunge_all()
        for batch_size in (1, 17, 200, 500):
            with self.subTest(batch_size=batch_size):
                self.assertEqual(self.ids(query, batch_size=batch_size), expected)
                self.db.expunge_all()

    def test_confirmed_sizes_are_selected_by_sold_garment_role(self):
        self.seed([
            self.row('top', sizes=['RU48'], feed_raw_json={'size_system': 'RU'}),
            self.row('bottom', title='Брюки прямые', category='брюки', sizes=['50'],
                     feed_raw_json={'size_system': 'RU'}),
            self.row('dress', title='Платье миди', category='платья', sizes=['46'],
                     feed_raw_json={'size_system': 'RU'}),
            self.row('outer', title='Куртка демисезонная', category='верхняя одежда', sizes=['48'],
                     feed_raw_json={'size_system': 'RU'}),
            self.row('shoes', title='Ботинки кожаные', category='обувь', sizes=['EU39']),
            self.row('wrong', title='Брюки', category='брюки', sizes=['48'])])
        fit = self.profile(top=('48', 'RU'), bottom=('50', 'RU'), one_piece=('46', 'RU'))
        self.assertEqual(self.ids(fit=fit), ['bottom', 'dress', 'outer', 'shoes', 'top'])

    def test_pagination_is_applied_once_before_hydrating_each_batch(self):
        self.seed([self.row(str(i)) for i in range(8)])
        query = self.query.offset(2).limit(3)
        expected = [product.id for product in self.db.scalars(query)]
        self.db.expunge_all()
        for batch in (1, 2, 200):
            with self.subTest(batch=batch):
                self.assertEqual(self.ids(query, batch_size=batch), expected)
                self.db.expunge_all()

    def test_possible_matches_still_require_unit_compound_and_variant_stock_proof(self):
        self.seed([
            self.row('good'),
            self.row('conflict', feed_raw_json={'size_system': 'EU'}),
            self.row('compound', feed_raw_json={'sizes': 'M/L'}),
            self.row('unavailable', available_sizes_detailed=[{'size': 'M', 'availability': 'unavailable'}]),
            self.row('unknown', title='Лоферы', category='обувь', sizes=['39'], feed_raw_json={})])
        candidates = list(iter_size_candidates(self.db, self.query, self.fit))
        self.assertEqual([p.id for p in candidates], ['compound', 'conflict', 'good', 'unavailable', 'unknown'])
        self.assertEqual(self.admitted_ids(candidates), ['good'])

    def test_untracked_live_json_mutations_are_preserved(self):
        self.seed([self.row('changed', sizes=['S']), self.row('cleared')])
        changed = self.db.get(Product, 'changed')
        cleared = self.db.get(Product, 'cleared')
        changed.available_sizes.append('M')
        cleared.available_sizes.clear()
        self.assertFalse(self.db.dirty)
        self.assertEqual(self.ids(), ['changed'])
        self.assertEqual(changed.available_sizes, ['S', 'M'])

    def test_late_in_place_profile_edit_changes_the_next_projected_candidate(self):
        self.seed([self.row('a', sizes=['M']), self.row('b', sizes=['L'])])
        scan = iter_size_candidates(self.db, self.query, self.fit, batch_size=1)
        try:
            self.assertEqual(next(scan).id, 'a')
            self.fit.size_profile_json['sizes']['top']['value'] = 'L'
            self.assertEqual([product.id for product in scan], ['b'])
        finally:
            scan.close()

    def test_size_only_prefilter_preserves_role_specific_reference_for_variant_sizes(self):
        variants = ['XS', 'M', 'L', 'RU48', 'EU39', 'W28', 'W28L30', '7,5', 'М', ' M ']
        rows = []
        for index, (title, category) in enumerate((('Рубашка', 'рубашки'),
                ('Брюки', 'брюки'), ('Платье', 'платья'), ('Куртка', 'верхний_слой'),
                ('Лоферы', 'обувь'), ('BAON model', 'обувь'))):
            for variant_index, variant in enumerate(variants):
                rows.append(self.row(f'{index}-{variant_index:02}', title=title,
                    category=category, sizes=[variant]))
        self.seed(rows)
        profiles = (self.profile(), self.profile(top=('48', 'RU'), bottom=('W28', 'WAIST'),
            one_piece=('L', 'LETTER'), shoes=('39', 'EU')),
            self.profile(shoes=('7.5', 'UK')))
        for fit in profiles:
            with self.subTest(fit=fit.size_profile_json):
                expected = []
                for product in self.db.scalars(self.query):
                    wanted, system = requested_size_for_product(fit, product)
                    if candidate_scan.may_match_flat_size(product, wanted, requested_system=system):
                        expected.append(product.id)
                self.db.expunge_all()
                self.assertEqual(self.ids(fit=fit, batch_size=7), expected)
                self.db.expunge_all()

    def test_dirty_role_change_uses_full_orm_path_with_autoflush_disabled(self):
        self.seed([self.row('changed', title='Платье', category='платья', sizes=['39'])])
        changed = self.db.get(Product, 'changed')
        changed.title = 'Лоферы'
        changed.category = 'обувь'
        self.assertTrue(self.db.dirty)
        self.assertEqual(self.ids(), ['changed'])
        self.assertEqual(changed.title, 'Лоферы')

    def test_hydration_rechecks_original_scope_after_projection(self):
        self.seed([self.row('eligible')])
        original_probe = candidate_scan.may_match_flat_size

        def change_stock_scope(product, wanted, **kwargs):
            possible = original_probe(product, wanted, **kwargs)
            self.db.execute(update(Product).where(Product.id == product.id).values(is_active=0))
            return possible

        with patch('app.services.catalog.candidate_scan.may_match_flat_size', side_effect=change_stock_scope):
            self.assertEqual(self.ids(), [])

    def test_early_close_releases_projection_cursor(self):
        self.seed([self.row('a'), self.row('b')])
        original_execute = self.db.execute
        results = []

        def record_result(*args, **kwargs):
            result = original_execute(*args, **kwargs)
            results.append(result)
            return result

        with patch.object(self.db, 'execute', side_effect=record_result):
            scan = iter_size_candidates(self.db, self.query, self.fit, batch_size=1)
            self.assertEqual(next(scan).id, 'a')
            self.assertFalse(results[0].closed)
            scan.close()
            self.assertTrue(results[0].closed)

    def test_unconfirmed_size_does_not_hydrate_products(self):
        self.seed([self.row('possible')])
        self.fit.size_profile_json['sizes']['top']['state'] = 'size_unknown'
        self.assertEqual(self.ids(), [])
        self.assertFalse(any(isinstance(value, Product) for value in self.db.identity_map.values()))

    def test_invalid_batch_size_is_rejected(self):
        for batch in (0, -1, 501):
            with self.subTest(batch=batch), self.assertRaises(ValueError):
                self.ids(batch_size=batch)


if __name__ == '__main__':
    unittest.main()
