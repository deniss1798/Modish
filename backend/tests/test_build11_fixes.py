from fit_fixtures import confirmed_fit
import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from unittest.mock import patch
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from app.db import Base
from app.models import User, FitProfile
from app.api.products import products_list
from app.services.outfit_quality import pair_compatible
from app.services.product_complements import complementary_products
from app.services import activation_service
from test_styling_regressions import product


class Build11Fixes(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        # PostgreSQL lower() supports Cyrillic; SQLite's built-in does not.
        event.listen(self.engine, 'connect', lambda connection, _: connection.create_function(
            'lower', 1, lambda value: value.lower() if value is not None else None))
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, autoflush=False)()
        self.user = User(id=str(uuid4()), email='build11@example.test', password_hash='unused')
        self.db.add(self.user)
        self.db.flush()
        self.fit = confirmed_fit(id=str(uuid4()), user_id=self.user.id, height_cm=170,
            gender_target='womenswear', clothing_size='M', budget_max=25000)
        self.db.add(self.fit)
        self.db.flush()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def item(self, title, category, **kwargs):
        kwargs = dict(gender_target='womenswear', available_sizes=['M'], brand='BAON', **kwargs)
        row = product(title, category, **kwargs)
        self.db.add(row)
        self.db.flush()
        return row

    def search(self, query, **kwargs):
        with patch('app.api.products.user_from_token', return_value=self.user):
            return products_list(q=query, db=self.db, credentials=object(), **kwargs)

    def test_search_title_category_brand_and_words_in_any_order(self):
        coat = self.item('Куртка (эко пух) BAON', 'верхний_слой')
        pants = self.item('Брюки прямые', 'брюки', colors=['black'])
        self.item('Платье миди', 'платья')
        for query, expected in [('куртка', {coat.id}), ('КУРТКИ baon', {coat.id}),
                ('BAON куртка', {coat.id}), ('верхняя одежда', {coat.id}),
                ('чёрные брюки', {pants.id}), ('BAON', {coat.id, pants.id} )]:
            with self.subTest(query=query):
                found = {p['id'] for p in self.search(query)}
                if query == 'BAON': self.assertEqual(len(found), 3)
                else: self.assertEqual(found, expected)

    def test_search_filters_before_pagination_and_preserves_gender_quality(self):
        now = datetime.now(timezone.utc)
        for i in range(55):
            self.item(f'Платье новое {i}', 'платья', created_at=now)
        wanted = self.item('Джинсы прямые', 'джинсы', created_at=now-timedelta(days=5))
        bad = self.item('Джинсы без фото', 'джинсы', image_url='')
        male = self.item('Джинсы мужские', 'джинсы')
        male.gender_target = 'menswear'
        self.db.flush()
        self.assertEqual([p['id'] for p in self.search('джинсы', limit=1)], [wanted.id])
        self.assertEqual(self.search('джинсы', offset=1), [])
        self.assertNotIn(bad.id, [p['id'] for p in self.search('джинсы')])

    def test_search_does_not_treat_wildcards_or_cross_selling_as_matches(self):
        self.item('Рубашка белая', 'рубашки', description='Носите с брюками и ботинками')
        denim_jacket = self.item('Куртка джинсовая', 'верхний_слой')
        for query in ['несуществующаявещь', '%', 'ботинки', 'джинсы']:
            with self.subTest(query=query):
                self.assertEqual(self.search(query), [])
        # A literal underscore exists in this category; it must not match everything.
        self.assertEqual([p['id'] for p in self.search('_')], [denim_jacket.id])

    def test_warm_coat_rejects_open_and_light_shoes_without_season_metadata(self):
        coat = product('Куртка (эко пух) BAON', 'верхний_слой')
        for title in ['Сандалии Caprice', 'Босоножки', 'Туфли кожаные', 'Балетки', 'Мюли']:
            shoe = product(title, 'обувь')
            with self.subTest(title=title):
                self.assertFalse(pair_compatible(coat, shoe))
                self.assertFalse(pair_compatible(shoe, coat))
        self.assertTrue(pair_compatible(coat, product('Ботинки кожаные', 'обувь')))

    def test_cardigan_and_light_coat_keep_suitable_closed_shoes(self):
        flats = product('Балетки', 'обувь')
        self.assertTrue(pair_compatible(product('Кардиган ажурный', 'джемперы'), flats))
        self.assertTrue(pair_compatible(product('Плащ лёгкий', 'верхний_слой'), flats))
        self.assertFalse(pair_compatible(product('Куртка', 'верхний_слой',
            raw_params_json={'Наполнитель': 'Пух'}), flats))
        # Cross-sell descriptions are deliberately not garment attributes.
        self.assertTrue(pair_compatible(product('Кардиган', 'джемперы',
            description='Для холодных дней дополните пуховой курткой'), flats))

    def test_activation_complements_exclude_sandals_from_puffer(self):
        activation_service.configure(self.db, self.user, gender_target='womenswear', age_group='25-34', height=170,
            clothing_size='M', shoe_size='38', styles=['casual','minimalism'], price_segment='mid',
            clothing_size_system='LETTER', shoe_size_system='EU')
        def add(title, category, sizes):
            row = product(title, category, gender_target='womenswear', source='baon',
                available_sizes=sizes, style_tags=['casual'],
                feed_raw_json={'size_system':'EU' if category=='обувь' else 'LETTER'},
                product_url='https://baon.ru/catalog/' + str(uuid4()))
            self.db.add(row); self.db.flush(); return row
        coat = add('Куртка (эко пух)', 'верхний_слой', ['M'])
        sandals = add('Сандалии', 'обувь', ['38'])
        boots = add('Ботинки кожаные', 'обувь', ['38'])
        add('Брюки прямые', 'брюки', ['M'])
        rows = complementary_products(self.db, self.user, coat)
        self.assertIn(boots.id, [p.id for p in rows])
        self.assertNotIn(sandals.id, [p.id for p in rows])
