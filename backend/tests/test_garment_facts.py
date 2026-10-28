"""Current sold-item fields drive role and outfit admission after edits."""
from types import SimpleNamespace
import unittest

from app.models import Product
from app.services.garment_roles import product_role
from app.services.outfit_quality import garment_text, scenario_product_ok


class GarmentFactsTests(unittest.TestCase):
    def test_live_title_and_each_category_leaf_change_the_sold_role(self):
        row = Product(title='Рубашка к юбке', category='рубашки')
        self.assertEqual(product_role(row), 'top')
        self.assertEqual(product_role(row), 'top')
        row.title = 'Юбка с блузкой на фото'
        self.assertEqual(product_role(row), 'bottom')
        row.title = 'BAON model'
        self.assertEqual(product_role(row), 'top')
        row.subcategory = 'обувь'
        self.assertEqual(product_role(row), 'shoes')
        row.subcategory = 'брюки'
        self.assertEqual(product_role(row), 'bottom')
        row.subcategory = None
        row.category_name = 'платья'
        self.assertEqual(product_role(row), 'one_piece')
        row.category_name = 'костюмы'
        self.assertIsNone(product_role(row))
        row.category_name = None
        row.category = 'обувь'
        self.assertEqual(product_role(row), 'shoes')
        row.category = 'платья'
        self.assertEqual(product_role(row), 'one_piece')
        row.title = 'Рубашка к юбке'
        self.assertEqual(product_role(row), 'top')

    def test_each_current_garment_field_can_reject_and_restore_an_outfit(self):
        row = Product(title='Рубашка однотонная', category='рубашки')
        self.assertTrue(scenario_product_ok(row, 'daily'))
        self.assertTrue(scenario_product_ok(row, 'daily'))
        for field in ('title', 'category', 'category_name', 'subcategory',
                      'merchant_category', 'merchant_subcategory'):
            with self.subTest(field=field):
                previous = getattr(row, field)
                setattr(row, field, 'Детская одежда')
                self.assertFalse(scenario_product_ok(row, 'daily'))
                self.assertIn('детская одежда', garment_text(row))
                setattr(row, field, previous)
                self.assertTrue(scenario_product_ok(row, 'daily'))
                self.assertNotIn('детская одежда', garment_text(row))

    def test_navigation_and_cross_sell_still_describe_the_sold_item(self):
        fields = dict(title='Туфли кожаные', category='Обувь и аксессуары',
            category_name=None, subcategory=None, merchant_category=None,
            merchant_subcategory=None)
        row = Product(**fields, description='Дополните образ детской сумкой.')
        projection = SimpleNamespace(**fields)
        self.assertEqual(product_role(row), 'shoes')
        self.assertEqual(product_role(projection), 'shoes')
        self.assertEqual(garment_text(row), garment_text(projection))
        self.assertNotIn('аксессуар', garment_text(row))
        self.assertNotIn('детск', garment_text(row))
        self.assertTrue(scenario_product_ok(row, 'daily'))


if __name__ == '__main__':
    unittest.main()
