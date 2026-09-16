"""Regressions from the September 13 device report."""
import unittest
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from urllib.parse import quote
from uuid import uuid4
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db import Base
from app.models import Product, User, FitProfile
from app.catalog_normalize import product_gender_from_model
from app.services.catalog.rule_filters import product_gender_compatible
from app.services.catalog.affiliate_link_service import resolve_outbound_url
from app.services.catalog_filters import CatalogFilters
from app.services.recommendation_engine import generate_feed
from app.services.catalog.feed_row_mapper import row_to_normalized
from app.services.catalog.product_normalizer import normalized_to_dict


def product(**kwargs):
    pid = str(uuid4())
    fields = dict(id=pid, external_id=pid, source='vipavenue', title='Рубашка', brand='Brand', category='рубашки', price=2500,
        image_url=f'https://media2.vipavenue.ru/{pid}.jpg', product_url='https://vipavenue.ru/product/123/',
        gender_target='menswear', available_sizes=['M','50'], colors=['black'], is_active=1,is_available=1,is_deleted_from_feed=0)
    fields.update(kwargs)
    return Product(**fields)

class CatalogDeviceRegressions(unittest.TestCase):
    def test_merchant_gender_overrides_old_guessed_label(self):
        cases = [
            ({'raw_params_json':{'param_пол':'Женская'}, 'gender_target':'menswear'}, 'womenswear'),
            ({'raw_params_json':{'param_пол':'Мужская'}, 'gender_target':None}, 'menswear'),
            ({'product_url':'https://bednari.com/g/campaign/?ulp='+quote('https://befree.ru/zhenskaya/product/123/',safe=''), 'gender_target':None}, 'womenswear'),
            ({'product_url':'https://bednari.com/g/campaign/?ulp='+quote('https://befree.ru/muzhskaya/product/456/',safe=''), 'gender_target':None}, 'menswear'),
            ({'gender_target':None,'title':'Лоферы'}, None),
            ({'gender_target':None,'description':'- Женская куртка из ткани\nДополните мужской рубашкой.'}, 'womenswear'),
        ]
        for fields, expected in cases:
            with self.subTest(fields=fields):
                p=product(**fields)
                self.assertEqual(product_gender_from_model(p),expected)
                self.assertEqual(product_gender_compatible(p,'menswear'),expected=='menswear')
                self.assertEqual(product_gender_compatible(p,'womenswear'),expected=='womenswear')

    def test_conflicting_metadata_is_not_unisex(self):
        p=product(raw_params_json={'param_пол':'Женская'},product_url='https://befree.ru/muzhskaya/product/123/')
        self.assertIsNone(product_gender_from_model(p))
        self.assertFalse(product_gender_compatible(p,'menswear'))

    def test_expired_affiliate_link_unwraps_to_exact_merchant(self):
        for dest in ['https://befree.ru/zhenskaya/product/BF123/50', 'https://vipavenue.ru/product/123/?color=1&size=50']:
            wrapped='https://bednari.com/g/old-campaign/?ulp='+quote(dest,safe='')
            self.assertEqual(resolve_outbound_url(product(product_url=wrapped,original_url=wrapped,affiliate_url=wrapped)),dest)
        for invalid in ['javascript:alert(1)','https://bednari.com/g/expired/','https://bednari.com/g/x/?ulp=http%3A%2F%2F127.0.0.1%2F']:
            self.assertEqual(resolve_outbound_url(product(product_url=invalid)),'')

    def test_import_keeps_russian_gender_forms_and_befree_paths(self):
        source=SimpleNamespace(code='test')
        for row, expected in [
            ({'id':'1','name':'Бомбер','price':'1000','param_пол':'Женская'},'womenswear'),
            ({'id':'2','name':'Рубашка','price':'1000','url':'https://bednari.com/g/x/?ulp=https%3A%2F%2Fbefree.ru%2Fmuzhskaya%2Fproduct%2F1'},'menswear')]:
            self.assertEqual(normalized_to_dict(row_to_normalized(row,source=source))['gender_target'],expected)

    def test_filter_searches_beyond_first_page_and_preserves_constraints(self):
        engine=create_engine('sqlite:///:memory:')
        Base.metadata.create_all(engine)
        with sessionmaker(bind=engine)() as db:
            user=User(id=str(uuid4()),email='filters@example.test',password_hash='unused');db.add(user);db.flush()
            db.add(FitProfile(id=str(uuid4()),user_id=user.id,height_cm=180,gender_target='menswear',clothing_size='XL',budget_max=1000,interest_categories=['обувь']))
            db.add_all([product(colors=['white'], available_sizes=['S'], price=1500) for _ in range(720)])
            target=product(title='Чёрные брюки',category='брюки',price=2500,colors=['black'],available_sizes=['M','50'],created_at=datetime.now(timezone.utc)-timedelta(days=90),updated_at=datetime.now(timezone.utc)-timedelta(days=90))
            female=product(title='Чёрные брюки',category='брюки',raw_params_json={'param_пол':'Женская'},colors=['black'])
            db.add_all([target,female]);db.flush()
            for filters in [CatalogFilters(colors=('Черный',)),CatalogFilters(sizes=('50',)),CatalogFilters(categories=('bottoms',)),CatalogFilters(min_price=2000,max_price=3000),CatalogFilters(2000,3000,('bottoms',),('M',),('Черный',))]:
                result=generate_feed(db,user,filters=filters)
                self.assertEqual([r.product.id for r in result],[target.id])
            self.assertEqual(generate_feed(db,user,filters=CatalogFilters(colors=('Красный',))),[])
        engine.dispose()
