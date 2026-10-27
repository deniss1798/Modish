"""Complete the item being sold, without substituting it or inventing fit."""
from datetime import datetime, timezone
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import ActivationSession, FitProfile, User
from app.services.complement_policy import complement_scenario, complements_anchor
from app.services.fit_size_profile import update_size_profile
from app.services.garment_roles import product_role
from app.services.outfit_quality import pair_compatible
from app.services.product_complements import complementary_products
from fit_fixtures import confirmed_fit
from test_styling_regressions import product


class AnchorComplementTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        # Match PostgreSQL's Unicode lower() for Cyrillic title retrieval.
        event.listen(self.engine, 'connect', lambda connection, _: connection.create_function(
            'lower', 1, lambda value: value.lower() if value is not None else None))
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, autoflush=False)()
        self.user = User(id=str(uuid4()), email='anchor@example.test', password_hash='unused')
        self.fit = confirmed_fit(id=str(uuid4()), user_id=self.user.id, height_cm=170,
            gender_target='womenswear', clothing_size='M', budget_max=25000)
        update_size_profile(self.fit, sizes={'shoes': {'value': '38', 'system': 'EU'}})
        self.db.add_all([self.user, self.fit])
        self.db.flush()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def item(self, title, category, **changes):
        values = dict(gender_target='womenswear', available_sizes=['M'], source='baon',
            price=2500, colors=['black'], feed_raw_json={'size_system': 'LETTER'})
        values.update(changes)
        if category == 'обувь' and 'available_sizes' not in changes:
            values.update(available_sizes=['38'], feed_raw_json={'size_system': 'EU'})
        row = product(title, category, **values)
        self.db.add(row)
        self.db.flush()
        return row

    def test_sold_noun_overrides_wrong_category_and_later_cross_sell(self):
        for title, category, expected in [
            ('Юбка-миди из вискозы', 'рубашки', 'bottom'),
            ('Юбка с кардиганом на фото', 'джемперы', 'bottom'),
            ('Блузка к юбке', 'юбки', 'top'),
            ('Топ под платье', 'платья', 'top'),
            ('Свитер для ношения с пальто', 'верхний_слой', 'top'),
            ('Платье-рубашка', 'рубашки', 'one_piece'),
            ('Жакет женский', 'рубашки', 'outerwear'),
            ('Пуловер женский', 'другое', 'top'),
            ('Джинсовая куртка', 'джинсы', 'outerwear'),
            ('Костюмные брюки', 'костюмы', 'bottom'),
            ('Свитерное платье', 'джемперы', 'one_piece'),
            ('Жилетное платье', 'жилеты', 'one_piece'),
            ('Рубашечное платье', 'рубашки', 'one_piece'),
            ('Shirt dress', 'рубашки', 'one_piece'),
            ('Sweatshirt dress', 'худи', 'one_piece'),
            ('Dress shirt', 'платья', 'top'),
            ('Dress pants', 'платья', 'bottom'),
            ('DC Shoes Tank Circle Star', 'футболки', 'top'),
            ('Dress shoes', 'обувь', 'shoes'),
        ]:
            with self.subTest(title=title):
                self.assertEqual(product_role(self.item(title, category)), expected)

    def test_unmodeled_sold_item_is_not_a_reference_garment(self):
        for title in ('Ремень к юбке', 'Бюстгальтер-топ', 'Комплект блуза и юбка',
                      'Костюм с юбкой', 'Сумка для обуви', 'Полукомбинезон джинсовый'):
            with self.subTest(title=title):
                self.assertIsNone(product_role(self.item(title, 'рубашки')))

    def test_generic_model_title_uses_specific_category_leaf(self):
        shoe = self.item('BAON model', 'рубашки', subcategory='обувь')
        self.assertEqual(product_role(shoe), 'shoes')
        actual_top = self.item('Майка DC Shoes', 'рубашки', subcategory='обувь')
        self.assertEqual(product_role(actual_top), 'top')

    def test_skirt_complements_are_missing_roles_even_with_bad_category(self):
        anchor = self.item('Юбка из вискозы', 'рубашки')
        other_skirt = self.item('Юбка с кардиганом на фото', 'рубашки')
        blouse = self.item('Блузка к юбке', 'юбки')
        shoes = self.item('Туфли кожаные', 'обувь')
        rows = complementary_products(self.db, self.user, anchor)
        self.assertEqual({row.id for row in rows}, {blouse.id, shoes.id})
        self.assertNotIn(other_skirt.id, {row.id for row in rows})
        self.assertEqual([product_role(row) for row in rows], ['top', 'shoes'])

    def test_colour_bridge_precedes_price_similarity_without_banning_accents(self):
        anchor = self.item('Юбка синяя', 'юбки', colors=['blue'])
        accent = self.item('Блузка красная', 'рубашки', colors=['red'], price=2500)
        neutral = self.item('Блузка белая', 'рубашки', colors=['white'], price=2600)
        rows = complementary_products(self.db, self.user, anchor)
        self.assertEqual([row.id for row in rows], [neutral.id, accent.id])

    def test_activation_uses_anchor_occasion_and_never_bypasses_scenario(self):
        anchor = self.item('Платье вечернее', 'платья')
        pumps = self.item('Туфли кожаные', 'обувь')
        sneakers = self.item('Кеды повседневные', 'обувь')
        self.db.add(ActivationSession(user_id=self.user.id, swipes_json={},
            completed_at=datetime.now(timezone.utc)))
        self.db.flush()
        self.assertEqual(complement_scenario(anchor), 'evening')
        # Retrieve deliberately adversarial but individually size-valid rows.
        with patch('app.services.activation_service.candidate_pool', return_value=[sneakers, pumps]):
            rows = complementary_products(self.db, self.user, anchor)
        self.assertEqual([row.id for row in rows], [pumps.id])
        self.assertFalse(complements_anchor(anchor, sneakers))

    def test_warm_filling_from_either_raw_container_rejects_light_shoes(self):
        flats = self.item('Туфли кожаные', 'обувь')
        boots = self.item('Ботинки кожаные', 'обувь')
        for field in ('raw_params_json', 'feed_raw_json'):
            coat = self.item('Куртка женская', 'верхний_слой', **{field: {'Наполнитель': 'Пух'}})
            with self.subTest(field=field):
                self.assertFalse(pair_compatible(coat, flats))
                self.assertFalse(pair_compatible(flats, coat))
                self.assertTrue(pair_compatible(coat, boots))

    def test_unknown_shoe_unit_does_not_enter_complements(self):
        anchor = self.item('Юбка миди', 'юбки')
        unknown = self.item('Туфли кожаные', 'обувь', available_sizes=['38'], feed_raw_json={})
        confirmed = self.item('Туфли замшевые', 'обувь')
        rows = complementary_products(self.db, self.user, anchor)
        self.assertEqual([row.id for row in rows], [confirmed.id])
        self.assertNotIn(unknown.id, {row.id for row in rows})


if __name__ == '__main__':
    unittest.main()
