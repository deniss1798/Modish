"""Literal catalogue phrase search keeps noun and colour conditions separate."""
import unittest
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db import Base
from app.models import Product
from app.services.product_search import matches_search_product, search_conditions
from app.services.shopping_footwear import product_footwear_types


class PhraseProductSearchTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        self.addCleanup(self.engine.dispose)
        # PostgreSQL lower() handles Cyrillic in production. SQLite's built-in
        # lower() is ASCII-only; keep this isolated adapter equivalent here.
        with self.engine.connect() as connection:
            connection.connection.driver_connection.create_function('lower', 1,
                lambda value: value.lower() if isinstance(value, str) else value)
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.addCleanup(self.db.close)

    def product(self, **changes):
        pid = str(uuid4())
        values = dict(id=pid, external_id=pid, source='baon', title='Туфли',
            category='обувь', brand='BAON', price=6000, currency='RUB',
            image_url=f'https://baon.ru/{pid}.jpg', product_url=f'https://baon.ru/{pid}')
        values.update(changes)
        product = Product(**values)
        self.db.add(product)
        self.db.flush()
        return product

    def find(self, phrase):
        return set(self.db.scalars(select(Product.id).where(*search_conditions(phrase))))

    def test_blue_pumps_match_plural_and_genitive_but_not_other_shoes(self):
        good = self.product(title='Туфли синие')
        raw_color = self.product(title='Лодочки', color_original='navy')
        self.product(title='Сандалии синие')
        self.product(title='Лоферы синие')
        self.product(title='Туфли', color_original='red')
        self.product(title='Туфли', description='Рекомендуем носить с синими брюками')
        expected = {good.id, raw_color.id}
        self.assertEqual(self.find('Синие туфли'), expected)
        self.assertEqual(self.find('синих туфель'), expected)

    def test_short_color_stems_in_nouns_and_brands_are_not_color_matches(self):
        moccasins = self.product(title='Туфли-мокасины', color_original='black')
        grey_brand = self.product(title='Туфли', brand='Serginnetti', color_original='black')
        self.assertNotIn(moccasins.id, self.find('синие туфли'))
        self.assertNotIn(grey_brand.id, self.find('серые туфли'))

    def test_concrete_footwear_declensions_do_not_become_general_shoe_search(self):
        good = self.product(title='Кроссовки', color_original='white')
        self.product(title='Ботинки', color_original='white')
        self.assertEqual(self.find('белых кроссовок'), {good.id})

    def test_navigation_list_preserves_the_sold_noun_but_not_other_members(self):
        pumps = SimpleNamespace(title='Туфли кожаные', category='обувь',
            category_name='Туфли и мокасины')
        moccasins = SimpleNamespace(title='Мокасины кожаные', category='обувь',
            category_name='Туфли и мокасины')
        self.assertEqual(product_footwear_types(pumps), {'pumps'})
        self.assertEqual(product_footwear_types(moccasins), {'moccasins'})
        self.assertTrue(matches_search_product(pumps, 'синие туфли'))
        self.assertFalse(matches_search_product(moccasins, 'синие туфли'))
        for changes in ({'subcategory': 'Мокасины'}, {'feed_raw_json': {'shoe_type': 'moccasins'}}):
            with self.subTest(changes=changes):
                self.assertFalse(matches_search_product(SimpleNamespace(**vars(pumps), **changes), 'туфли'))
        unknown = SimpleNamespace(title='Модель Adelia', category='обувь',
            category_name='Туфли и мокасины')
        self.assertFalse(matches_search_product(unknown, 'туфли'))

    def test_multiword_english_footwear_keeps_its_specific_type(self):
        ankle = self.product(title='Blue ankle boots')
        generic = self.product(title='Blue boots')
        self.assertTrue(matches_search_product(ankle, 'blue ankle boots'))
        self.assertFalse(matches_search_product(generic, 'blue ankle boots'))
        self.assertFalse(matches_search_product(ankle, 'blue boots'))
        self.assertEqual(self.find('blue ankle boots'), {ankle.id})


if __name__ == '__main__':
    unittest.main()
