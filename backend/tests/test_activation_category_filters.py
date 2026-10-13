"""Explicit category intent must work for users who learned another category."""
import unittest
from collections import Counter
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import FitProfile, Product, TasteProfile, User
from app.services import activation_service as activation
from app.services.catalog_filters import CatalogFilters
from app.api.products import _feed_scored_items


class ActivationCategoryFilterTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, autoflush=False)()
        self.user = User(id=str(uuid4()), email='filters@example.test', password_hash='unused')
        self.db.add(self.user)
        self.db.flush()
        self.session = activation.configure(self.db, self.user, age_group='25-34', height=170,
            clothing_size='M', shoe_size='38', shoe_size_system='EU', styles=['casual', 'minimalism'], price_segment='mass')
        self.fit = self.db.scalar(select(FitProfile).where(FitProfile.user_id == self.user.id))
        self.fit.style_constraints = {'clothing_size_system': 'LETTER', 'shoe_size_system': 'EU'}
        self.fit.interest_categories = ['рубашки']
        self.taste = self.db.scalar(select(TasteProfile).where(TasteProfile.user_id == self.user.id))
        self.taste.liked_categories = ['рубашки']
        self.taste.category_weights = {'рубашки': 10}
        self.session.completed_at = datetime.now(timezone.utc)
        self.db.flush()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def product(self, category, **overrides):
        pid = str(uuid4())
        shoe = category == 'обувь'
        values = dict(id=pid, external_id=pid, source='befree', brand='A',
            title={'обувь': 'Лоферы кожаные', 'верхний_слой': 'Куртка демисезонная',
                   'рубашки': 'Рубашка однотонная'}[category], category=category,
            gender_target='womenswear', price=1500, currency='RUB',
            image_url=f'https://befree.ru/images/{pid}.jpg', product_url=f'https://befree.ru/product/{pid}',
            available_sizes=['38'] if shoe else ['M'], colors=['black'], style_tags=['casual'],
            is_active=1, is_available=1, availability_status='in_stock', is_deleted_from_feed=0,
            feed_raw_json={'size_system': 'EU' if shoe else 'LETTER'},
            last_seen_in_feed_at=datetime.now(timezone.utc))
        values.update(overrides)
        product = Product(**values)
        self.db.add(product)
        self.db.flush()
        return product

    def feed(self, *categories, **kwargs):
        return _feed_scored_items(self.db, self.user, limit=20,
            filters=CatalogFilters(categories=categories, **kwargs))

    def test_shoes_and_outerwear_override_learned_category_and_fill_page(self):
        self.product('рубашки')
        for category, key in [('обувь', 'shoes'), ('верхний_слой', 'outerwear')]:
            with self.subTest(category=category):
                expected = {self.product(category).id for _ in range(24)}
                rows = self.feed(key)
                self.assertEqual(len(rows), 20)
                self.assertEqual(len({r['product']['id'] for r in rows}), 20)
                self.assertTrue({r['product']['id'] for r in rows} <= expected)

    def test_filter_keeps_size_gender_price_and_availability_constraints(self):
        good = self.product('обувь')
        for overrides in [dict(gender_target='menswear'), dict(available_sizes=['39']),
                          dict(available_sizes=[]), dict(price=10001), dict(is_available=0),
                          dict(image_url=''), dict(product_url=''), dict(title='Обувь для девочек')]:
            self.product('обувь', **overrides)
        self.assertEqual([r['product']['id'] for r in self.feed('shoes')], [good.id])
        self.assertEqual(self.feed('shoes', max_price=1000), [])
        self.assertEqual(self.feed('shoes', colors=('red',)), [])

    def test_no_category_filter_still_uses_learned_interests(self):
        shirt = self.product('рубашки')
        self.product('обувь')
        self.product('верхний_слой')
        for filters in [CatalogFilters(), CatalogFilters(max_price=3000)]:
            rows = _feed_scored_items(self.db, self.user, limit=20, filters=filters)
            self.assertEqual([r['product']['id'] for r in rows], [shirt.id])

    def test_filtered_page_prefers_multiple_brands_before_fallback(self):
        for brand in ['A', 'B', 'C']:
            for _ in range(20):
                self.product('обувь', brand=brand)
        rows = self.feed('shoes')
        self.assertEqual(len(rows), 20)
        self.assertLessEqual(max(Counter(r['product']['brand'] for r in rows).values()), 7)

    def test_multiple_categories_and_exclusions_are_honoured(self):
        self.product('рубашки')
        for category in ['обувь', 'верхний_слой']:
            for _ in range(22):
                self.product(category)
        rows = self.feed('shoes', 'outerwear')
        self.assertEqual(len(rows), 20)
        self.assertEqual({r['product']['category'] for r in rows}, {'обувь', 'верхний_слой'})
        second = _feed_scored_items(self.db, self.user, limit=20,
            filters=CatalogFilters(categories=('shoes', 'outerwear')),
            exclude_ids=[r['product']['id'] for r in rows])
        self.assertEqual(len(second), 20)
        self.assertFalse({r['product']['id'] for r in rows} & {r['product']['id'] for r in second})


if __name__ == '__main__':
    unittest.main()
