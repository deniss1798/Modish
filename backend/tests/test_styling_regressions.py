import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker
from app.db import Base
from app.models import User, FitProfile, Product, ProductEmbedding, RecommendationEventV2, Outfit
from app.services.catalog_audience import product_is_adult
from app.services.garment_roles import product_role
from app.services.outfit_quality import pair_compatible, scenario_product_ok
from app.services.product_complements import complementary_products
from app.services.embedding_service import build_user_taste_embedding, upsert_product_embedding
from app.services.recommendation_engine import generate_feed
from app.services.mie_scoring import compute_fit_score
from app.api.outfits import visible_outfits


def product(title='Рубашка мужская', category='рубашки', **kwargs):
    pid = str(uuid4())
    values = dict(id=pid, external_id=pid, source='vipavenue', title=title, category=category,
        brand='Test', price=2500, image_url=f'https://media2.vipavenue.ru/{pid}.jpg',
        product_url=f'https://vipavenue.ru/product/{pid}/', gender_target='menswear',
        available_sizes=['XL'], colors=['black'], is_active=1, is_available=1, is_deleted_from_feed=0)
    values.update(kwargs)
    return Product(**values)


class StylingRegressions(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, autoflush=False)()
        self.user = User(id=str(uuid4()), email='styling@example.test', password_hash='unused')
        self.fit = FitProfile(id=str(uuid4()), user_id=self.user.id, height_cm=180,
            gender_target='menswear', clothing_size='XL', budget_max=30000)
        self.db.add(self.user); self.db.flush(); self.db.add(self.fit); self.db.flush()

    def tearDown(self):
        self.db.close(); self.engine.dispose()

    def test_children_excluded_by_title_age_and_height(self):
        for fields in [dict(title='Футболка для мальчиков оверсайз FILA'),
                dict(title='Рубашка для подростков'), dict(raw_params_json={'Возраст':'Дети'}),
                dict(title='Junior shirt'), dict(available_sizes=['122-128','134-140','170-176']),
                dict(available_sizes=['128','140','164'])]:
            with self.subTest(fields=fields):
                p = product(**fields)
                self.assertFalse(product_is_adult(p))
                self.db.add(p)
        adult = product(title='Футболка мужская'); self.db.add(adult); self.db.flush()
        rows = generate_feed(self.db, self.user, limit=30)
        self.assertEqual([r.product.id for r in rows], [adult.id])

    def test_roles_describe_sold_item_and_exclude_ambiguous_sets(self):
        for title, cat, role in [('Поло хлопковое','другое','top'), ('Брюки мужские','верхний_слой','bottom'),
                ('Полукомбинезон джинсовый','комбинезоны',None), ('Костюм мужской','костюмы',None),
                ('Платье миди','платья','one_piece'), ('Лоферы кожаные','обувь','shoes'), ('Dress shoes','обувь','shoes'),
                ('Майка мужская DC Shoes Tank Circle Star', 'футболки', 'top')]:
            self.assertEqual(product_role(product(title, cat)), role)

    def test_sport_pants_and_formal_shoes_are_incompatible(self):
        trousers = product('Брюки мужские Diadora', 'брюки', source='sportmaster',
            description='Спортивные брюки из мягкого трикотажа.')
        loafers = product('Лоферы кожаные', 'обувь')
        shirt = product()
        self.assertFalse(pair_compatible(trousers, loafers))
        self.assertFalse(pair_compatible(trousers, shirt))
        self.assertTrue(pair_compatible(trousers, product('Basic t-shirt', 'футболки')))
        for p in [trousers, product('Рубашка FILA', source='sportmaster')]:
            self.assertFalse(scenario_product_ok(p, 'office'))
        self.assertTrue(pair_compatible(product('Брюки классические','брюки'), loafers))
        self.assertFalse(scenario_product_ok(product('Брюки горнолыжные Columbia','брюки'), 'daily'))
        self.assertFalse(scenario_product_ok(product('Боди-стринг с люрексом'), 'office'))
        self.assertFalse(scenario_product_ok(product('Майка в рубчик'), 'evening'))
        self.assertFalse(pair_compatible(product('Футболка'), product('Ботинки на овчине','обувь')))

    def test_complements_balance_missing_roles_and_never_pad_same_role(self):
        anchor = product('Поло хлопковое', 'другое')
        bottom = product('Брюки хлопковые', 'брюки')
        shoes = product('Кеды мужские', 'обувь', available_sizes=['42'])
        kids = product('Брюки для мальчиков', 'брюки')
        self.db.add_all([anchor, bottom, shoes, kids] + [product() for _ in range(8)]); self.db.flush()
        rows = complementary_products(self.db, self.user, anchor)
        self.assertEqual({p.id for p in rows}, {bottom.id, shoes.id})
        self.assertEqual([product_role(p) for p in rows], ['bottom','shoes'])
        bottom.is_active = 0; shoes.is_active = 0; self.db.flush()
        self.assertEqual(complementary_products(self.db, self.user, anchor), [])

    def test_old_overalls_outfit_is_hidden(self):
        overall = product('Полукомбинезон джинсовый','комбинезоны')
        shoes = product('Лоферы','обувь')
        self.db.add_all([overall, shoes]); self.db.flush()
        outfit = Outfit(id=str(uuid4()), user_id=self.user.id, style_direction='office',
            items_json={'one_piece':overall.id, 'shoes':shoes.id})
        self.assertEqual(visible_outfits(self.db, self.user, [outfit]), [])

    def test_size_variants_cannot_exhaust_feed_before_remaining_catalogue(self):
        now = datetime.now(timezone.utc)
        variants = [product(f'Рубашка вариант {i}', image_url='https://media2.vipavenue.ru/shared.jpg',
            updated_at=now, created_at=now) for i in range(720)]
        alternatives = [product(f'Рубашка другая {i}', updated_at=now-timedelta(days=2),
            created_at=now-timedelta(days=2)) for i in range(35)]
        self.db.add_all(variants + alternatives); self.db.flush()
        rows = generate_feed(self.db, self.user, limit=30)
        self.assertEqual(len(rows), 30)
        self.assertEqual(len({r.product.image_url for r in rows}), 30)

    def test_size_badge_requires_exact_confirmed_size(self):
        for sizes, expected in [(['XL'],True), (['L'],False), (['50'],False), ([],False)]:
            _, _, reasons, _ = compute_fit_score(product(available_sizes=sizes),
                fit=self.fit, palette=set(), avoid_colors=set())
            self.assertEqual('Есть ваш размер' in reasons, expected)

    def test_taste_read_does_not_insert_embeddings_and_upsert_is_idempotent(self):
        p = product(); self.db.add(p); self.db.flush()
        self.db.add(RecommendationEventV2(id=str(uuid4()), user_id=self.user.id,
            product_id=p.id, event_type='like', meta_json={})); self.db.flush()
        taste = build_user_taste_embedding(self.db, user_id=self.user.id)
        self.assertIsNotNone(taste.positive_centroid)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(ProductEmbedding)), 0)
        first = upsert_product_embedding(self.db, p)
        original_id = first.id
        p.title = 'Рубашка новая'; self.db.flush()
        second = upsert_product_embedding(self.db, p)
        self.assertEqual(second.id, original_id)
        self.assertIn('новая', second.text_snapshot)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(ProductEmbedding)), 1)
