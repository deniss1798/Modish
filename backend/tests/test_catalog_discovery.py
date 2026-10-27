"""Browse-only discovery keeps every hard gate except exact size admission."""
import base64
from datetime import datetime, timedelta, timezone
import json
import unittest
import weakref
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, insert, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.catalog_discovery import router
from app.api.deps import create_access_token, get_db
from app.api.onboarding import router as onboarding_router
from app.api.products import router as products_router
from app.db import Base
from app.models import (ActivationSession, ActivationExposure, AffiliateClick,
    FitProfile, MetricEvent, Product, ProductImpression, RecommendationEventV2,
    ShoppingIntent, SourceRule, StyleProfile, TasteProfile, User, UserProductState,
    UserTasteFeature)


class CatalogueDiscoveryTests(unittest.TestCase):
  def setUp(self):
    self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    self.addCleanup(self.engine.dispose)
    Base.metadata.create_all(self.engine)
    self.sessions = sessionmaker(bind=self.engine, autoflush=False)
    self.db = self.sessions()
    self.addCleanup(self.db.close)
    self.user = User(id='discovery-user', email='discovery@example.test', password_hash='unused')
    self.other = User(id='other-discovery', email='other@example.test', password_hash='unused')
    self.fit = FitProfile(id='fit', user_id=self.user.id, height_cm=170,
        gender_target='womenswear', budget_max=0)
    self.db.add_all([self.user, self.other, self.fit,
        FitProfile(id='other-fit', user_id=self.other.id, height_cm=170, gender_target='womenswear')])
    self.db.commit()
    self.headers = {'Authorization': 'Bearer ' + create_access_token(self.user.id)}
    self.other_headers = {'Authorization': 'Bearer ' + create_access_token(self.other.id)}
    app = FastAPI()
    app.include_router(router)
    app.include_router(products_router)
    app.include_router(onboarding_router)
    def database():
      with self.sessions() as db:
        yield db
    app.dependency_overrides[get_db] = database
    self.client = TestClient(app)
    self.addCleanup(self.client.close)

  def fields(self, pid, **changes):
    values = dict(id=pid, external_id=pid, source='shoppinglive', brand='Caprice',
        title='лоферы кожаные', category='обувь', category_name='Обувь',
        gender_target='womenswear', price=1500, currency='RUB', available_sizes=['39'],
        feed_raw_json={}, image_url=f'https://catalog.example.test/{pid}.jpg',
        product_url=f'https://catalog.example.test/p/{pid}',
        is_active=1, is_available=1, availability_status='in_stock', is_deleted_from_feed=0,
        last_seen_in_feed_at=datetime.now(timezone.utc))
    values.update(changes)
    return values

  def product(self, pid, **changes):
    value = Product(**self.fields(pid, **changes))
    self.db.add(value)
    self.db.commit()
    return value

  def get(self, **params):
    return self.client.get('/catalog/discovery', params=params, headers=self.headers)

  def ids(self, response):
    self.assertEqual(response.status_code, 200, response.text)
    return [p['id'] for p in response.json()['products']]

  def detail(self, pid):
    return self.client.get(f'/catalog/discovery/{pid}', headers=self.headers)

  def count_snapshot(self):
    models = (FitProfile, StyleProfile, TasteProfile, ActivationSession,
        ActivationExposure, UserTasteFeature, RecommendationEventV2,
        ProductImpression, MetricEvent, ShoppingIntent, UserProductState, AffiliateClick)
    return {model.__name__: self.db.scalar(select(func.count()).select_from(model)) for model in models}

  def test_cold_user_waits_for_gender_without_creating_taste_fit_or_activation(self):
    self.db.delete(self.fit)
    self.db.commit()
    self.product('unknown-unit', size_system='EU')  # Old guessed column is not proof.
    before = self.count_snapshot()
    response = self.get(category='shoes')
    self.assertEqual(self.ids(response), [])
    body = response.json()
    self.assertEqual(body['scope'], {'gender_target': None,
        'gender_basis': 'missing', 'budget_max': 10000, 'budget_basis': 'catalogue_default'})
    self.assertFalse(body['personalized'])
    self.assertEqual(body['mode'], 'catalogue_discovery')
    self.assertIsNone(body['next_cursor'])
    self.assertFalse(body['has_more'])
    self.assertEqual(self.detail('unknown-unit').status_code, 404)
    self.assertEqual(self.client.get('/feed', headers=self.headers).status_code, 409)
    self.assertEqual(self.count_snapshot(), before)

  def test_selected_gender_browses_without_confirming_sizes_or_training_taste(self):
    self.product('unknown-unit', size_system='EU')
    before = self.count_snapshot()
    response = self.get(category='shoes')
    self.assertEqual(self.ids(response), ['unknown-unit'])
    product = response.json()['products'][0]
    self.assertTrue(product['catalogue_discovery'])
    self.assertEqual(product['size_verification'], 'not_verified')
    self.assertEqual(product['size_notice'], 'Размер нужно уточнить у магазина')
    self.assertEqual(product['merchant_sizes'], [{'raw_size': '39', 'normalized_size': None,
        'size_system': 'UNKNOWN', 'availability': 'available'}])
    self.assertIsNone(product['size_system'])
    self.assertFalse(any(k in product for k in ('reasons', 'fit_score', 'size_matches')))
    self.assertEqual(self.detail('unknown-unit').status_code, 200)
    self.assertEqual(self.client.get('/feed', headers=self.headers).status_code, 409)
    self.assertEqual(self.count_snapshot(), before)

  def test_unconfirmed_numeric_profile_does_not_bypass_strict_feed(self):
    fit = self.fit
    fit.clothing_size = '48'
    fit.budget_max = 12000
    fit.size_profile_json = {'sizes': {'top': {'value': '48', 'system': None, 'state': 'size_unknown'}}}
    self.db.commit()
    original = json.dumps(fit.size_profile_json, sort_keys=True)
    self.product('numeric-unknown')
    response = self.get()
    self.assertEqual(self.ids(response), ['numeric-unknown'])
    self.assertEqual(response.json()['scope']['budget_basis'], 'profile')
    self.assertEqual(self.client.get('/feed', headers=self.headers).status_code, 409)
    self.db.refresh(fit)
    self.assertEqual(json.dumps(fit.size_profile_json, sort_keys=True), original)

  def test_non_size_quality_gates_cannot_be_relaxed_by_discovery(self):
    now = datetime.now(timezone.utc)
    cases = [
        {'title': 'лоферы для девочек'}, {'raw_params_json': {'Возраст': 'детский'}},
        {'available_sizes': ['122-128', '134-140']}, {'title': 'сыворотка для лица'},
        {'title': 'подушка'}, {'title': 'сумка', 'category': 'аксессуары', 'category_name': ''},
        {'gender_target': 'menswear'}, {'gender_target': 'unknown'},
        {'title': 'лоферы мужские', 'gender_target': 'unisex'},
        {'price': 1}, {'price': 10001}, {'price': 25001},
        {'image_url': ''}, {'image_url': 'https://[broken'},
        {'image_url': 'http://127.0.0.1/photo.jpg'}, {'product_url': ''},
        {'product_url': 'https://ad.admitad.com/g/opaque/'},
        {'is_active': 0}, {'is_available': 0}, {'is_deleted_from_feed': 1},
        {'availability_status': 'unknown'}, {'availability_status': 'out_of_stock'},
        {'last_seen_in_feed_at': None}, {'last_seen_in_feed_at': now - timedelta(hours=73)},
        {'last_seen_in_feed_at': now + timedelta(minutes=6)}, {'currency': 'USD'},
        {'source': 'vipavenue'}, {'source': 'demo'}, {'source': 'demix'}, {'source': ''},
        {'description': 'Обувь для фитнеса.'}, {'source_id': 'blocked-source', 'brand': 'Blocked'},
    ]
    self.db.add(SourceRule(id='rule', source_id='blocked-source', rule_type='blocked_brand',
        rule_value='Blocked', is_active=1))
    self.db.execute(insert(Product), [self.fields(f'a-{index:03}', **case)
                                     for index, case in enumerate(cases)])
    self.db.commit()
    self.product('z-good')
    self.assertEqual(self.ids(self.get(category='shoes', max_price=999999)), ['z-good'])
    for index in range(len(cases)):
      with self.subTest(case=cases[index]):
        self.assertEqual(self.detail(f'a-{index:03}').status_code, 404)

  def test_literal_search_category_source_and_price_apply_before_page(self):
    self.product('a-other-brand', brand='Other')
    self.product('b-over-filter', price=2001)
    self.product('c-other-source', source='baon')
    self.product('d-coat', title='куртка утеплённая', category='верхняя одежда')
    self.product('e-good', price=1800)
    self.assertEqual(self.ids(self.get(category='shoes', q='CAPRICE', source='SHOPPINGLIVE',
        min_price=1600, max_price=2000, limit=1)), ['e-good'])
    self.assertEqual(self.ids(self.get(category='верхний_слой')), ['d-coat'])
    self.assertEqual(self.ids(self.get(category='обувь', q='50%_')), [])
    self.assertEqual(self.ids(self.get(source='demo')), [])

  def _seed_precise_shoe_search(self):
    # All rows satisfy broad SQL retrieval; only the two explicit, consistent
    # sold nouns may reach a page. Equal timestamps make legacy order stable.
    now = datetime.now(timezone.utc)
    common = dict(color_original='blue', category_name='туфли и мокасины', created_at=now,
        available_sizes=['EU39'], feed_raw_json={'size_system': 'EU'})
    cases = [('a-moccasins', {'title': 'мокасины кожаные'}),
        ('b-conflict', {'title': 'туфли кожаные', 'subcategory': 'мокасины'}),
        ('c-ambiguous', {'title': 'модель Adelia'}),
        ('d-good', {'title': 'туфли кожаные'}),
        ('e-moccasins', {'title': 'мокасины кожаные'}),
        ('f-good', {'title': 'туфли кожаные'})]
    for pid, changes in cases:
      self.product(pid, **common, **changes)
    return ['d-good', 'f-good']

  def test_footwear_search_filters_before_discovery_cursor_and_has_more(self):
    expected = self._seed_precise_shoe_search()
    first = self.get(q='синие туфли', limit=1)
    self.assertEqual(self.ids(first), expected[:1])
    self.assertTrue(first.json()['has_more'])
    second = self.get(q='синие туфли', limit=1, cursor=first.json()['next_cursor'])
    self.assertEqual(self.ids(second), expected[1:])
    self.assertFalse(second.json()['has_more'])
    self.assertIsNone(second.json()['next_cursor'])

  def test_footwear_search_filters_before_legacy_http_offset_and_limit(self):
    from app.services.fit_size_profile import update_size_profile
    expected = self._seed_precise_shoe_search()
    self.fit.budget_max = 10000
    update_size_profile(self.fit, sizes={'shoes': {'value': '39', 'system': 'EU'}})
    self.db.commit()
    self.assertIsNone(self.db.get(ActivationSession, self.user.id))
    for offset, ids in ((0, expected[:1]), (1, expected[1:]), (2, [])):
      response = self.client.get('/products', headers=self.headers,
          params={'q': 'синие туфли', 'limit': 1, 'offset': offset})
      self.assertEqual(response.status_code, 200, response.text)
      self.assertEqual([row['id'] for row in response.json()], ids)

  def test_footwear_search_filters_before_personalized_http_ranking_and_offset(self):
    from app.services.activation_service import configure
    expected = list(reversed(self._seed_precise_shoe_search()))
    activation = configure(self.db, self.user, gender_target='womenswear', age_group='25-34',
        height=170, clothing_size='M', clothing_size_system='LETTER', shoe_size='39',
        shoe_size_system='EU', styles=['casual', 'minimalism'], price_segment='mass')
    activation.completed_at = datetime.now(timezone.utc)
    self.db.commit()
    before = self.count_snapshot()
    for offset, ids in ((0, expected[:1]), (1, expected[1:]), (2, [])):
      response = self.client.get('/products', headers=self.headers,
          params={'q': 'синие туфли', 'limit': 1, 'offset': offset})
      self.assertEqual(response.status_code, 200, response.text)
      self.assertEqual([row['id'] for row in response.json()], ids)
    self.assertEqual(self.count_snapshot(), before)

  def test_server_ceiling_never_expands_with_request_or_premium_profile(self):
    self.fit.budget_max = 100000
    self.db.commit()
    self.product('under-cap', price=24999)
    self.product('over-cap', price=25001)
    response = self.get(max_price=1000000)
    self.assertEqual(self.ids(response), ['under-cap'])
    self.assertEqual(response.json()['scope']['budget_max'], 25000)
    self.assertEqual(self.ids(self.get(max_price=1000)), [])
    self.assertEqual(self.ids(self.get(min_price=30000)), [])

  def test_known_profile_gender_is_respected_without_size_confirmation(self):
    self.fit.gender_target = 'menswear'
    self.fit.budget_max = 10000
    self.db.commit()
    self.product('women')
    self.product('men', gender_target='menswear', title='лоферы мужские')
    self.product('unisex', gender_target='unisex', title='ботинки унисекс')
    response = self.get()
    self.assertEqual(self.ids(response), ['men', 'unisex'])
    self.assertEqual(response.json()['scope']['gender_basis'], 'profile')

  def test_aliases_across_sparse_pages_do_not_return_or_create_phantom_empty_page(self):
    self.product('a', original_url='https://shop.example.test/p/model?size=39&utm_source=one')
    self.product('b-alias', original_url='https://shop.example.test/p/model?size=40&utm_source=two')
    self.product('c')
    self.product('d-alias', image_url='https://catalog.example.test/c.jpg?width=800')
    self.product('e')
    first = self.get(limit=1)
    self.assertEqual(self.ids(first), ['a'])
    self.assertTrue(first.json()['has_more'])
    second = self.get(limit=1, cursor=first.json()['next_cursor'])
    self.assertEqual(self.ids(second), ['c'])
    third = self.get(limit=1, cursor=second.json()['next_cursor'])
    self.assertEqual(self.ids(third), ['e'])
    self.assertFalse(third.json()['has_more'])
    self.assertIsNone(third.json()['next_cursor'])
    self.assertEqual(self.ids(self.get(limit=1, cursor=first.json()['next_cursor'])), ['c'])

  def test_late_eligible_winner_after_1501_bad_rows_and_orm_memory_is_batched(self):
    self.db.execute(insert(Product), [self.fields(f'{index:05}', title='лоферы для девочек',
        description='Merchant payload ' * 100) for index in range(1501)])
    self.db.execute(insert(Product), [self.fields('z-late')])
    self.db.commit()
    references, maximum = [], [0]
    def loaded(product, _context):
      references.append(weakref.ref(product))
      maximum[0] = max(maximum[0], sum(ref() is not None for ref in references))
    event.listen(Product, 'load', loaded)
    try:
      response = self.get(limit=20)
      self.assertEqual(self.ids(response), ['z-late'])
    finally:
      event.remove(Product, 'load', loaded)
    self.assertGreater(len(references), 1500)
    self.assertLess(maximum[0], 500, 'A sparse catalogue must not retain every ORM blob.')

  def test_malformed_foreign_or_filter_changed_cursor_is_rejected(self):
    self.product('a')
    self.product('b')
    cursor = self.get(limit=1).json()['next_cursor']
    for params in ({'cursor': '!!!'}, {'cursor': cursor, 'category': 'shoes'},
                   {'cursor': cursor, 'q': 'caprice'}, {'cursor': cursor, 'max_price': 5000}):
      with self.subTest(params=params):
        self.assertEqual(self.get(**params).status_code, 422)
    foreign = self.client.get('/catalog/discovery', headers=self.other_headers, params={'cursor': cursor})
    self.assertEqual(foreign.status_code, 422)
    decoded = json.loads(base64.urlsafe_b64decode(cursor + '=' * (-len(cursor) % 4)))
    decoded['after'] = 'unknown-product'
    invalid = base64.urlsafe_b64encode(json.dumps(decoded).encode()).decode().rstrip('=')
    self.assertEqual(self.get(cursor=invalid).status_code, 422)

  def test_cursor_target_becoming_stale_requires_reload_and_detail_revalidates(self):
    first = self.product('a')
    self.product('b')
    cursor = self.get(limit=1).json()['next_cursor']
    first.last_seen_in_feed_at = datetime.now(timezone.utc) - timedelta(hours=73)
    self.db.commit()
    self.assertEqual(self.get(cursor=cursor).status_code, 422)
    self.assertEqual(self.detail('a').status_code, 404)
    self.assertEqual(self.ids(self.get()), ['b'])

  def test_hidden_identity_and_ownership_are_respected_without_new_exposure(self):
    hidden = self.product('a-hidden')
    self.product('b-hidden-alias', image_url=hidden.image_url + '?width=800')
    self.product('c-visible')
    self.db.add(UserProductState(id='hidden', user_id=self.user.id, product_id=hidden.id,
        hidden_until=datetime.now(timezone.utc) + timedelta(days=1)))
    self.db.add(UserProductState(id='other-hidden', user_id=self.other.id, product_id='c-visible',
        hidden_until=datetime.now(timezone.utc) + timedelta(days=1)))
    self.db.commit()
    before = self.count_snapshot()
    self.assertEqual(self.ids(self.get()), ['c-visible'])
    self.assertEqual(self.detail('b-hidden-alias').status_code, 404)
    self.assertEqual(self.detail('c-visible').status_code, 200)
    self.assertEqual(self.count_snapshot(), before)

  def test_disliked_legacy_invalid_identity_does_not_break_catalogue(self):
    self.product('legacy', image_url='https://[broken')
    self.product('good')
    self.db.add(ActivationSession(user_id=self.user.id, swipes_json={'legacy': 'dislike'}))
    self.db.commit()
    self.assertEqual(self.ids(self.get()), ['good'])

  def test_merchant_sizes_retain_units_conflicts_and_variant_uncertainty(self):
    self.product('known', available_sizes=['EU39'], feed_raw_json={'size_system': 'EU'})
    self.product('conflict', feed_raw_json={'size_system': ['EU', 'RU']})
    self.product('uncertain-stock', feed_raw_json={'size_system': 'EU'},
        available_sizes_detailed=[{'raw_size': '39', 'availability': 'unknown'}])
    rows = {p['id']: p for p in self.get().json()['products']}
    self.assertEqual(rows['known']['merchant_sizes'][0]['size_system'], 'EU')
    self.assertEqual(rows['known']['merchant_sizes'][0]['raw_size'], 'EU39')
    self.assertEqual(rows['conflict']['merchant_sizes'][0]['size_system'], 'UNKNOWN')
    self.assertEqual(rows['uncertain-stock']['merchant_sizes'][0]['availability'], 'unknown')
    self.assertTrue(all(p['size_verification'] == 'not_verified' for p in rows.values()))

  def test_detail_revalidates_changed_price_source_and_public_url(self):
    product = self.product('changing')
    self.assertEqual(self.detail(product.id).status_code, 200)
    for field, invalid, original in [('price', 10001, 1500), ('source', 'vipavenue', 'shoppinglive'),
                                     ('image_url', 'http://localhost/a.jpg', product.image_url)]:
      with self.subTest(field=field):
        setattr(product, field, invalid)
        self.db.commit()
        self.assertEqual(self.detail(product.id).status_code, 404)
        setattr(product, field, original)
        self.db.commit()
    self.assertEqual(self.detail('missing').status_code, 404)

  def test_authenticated_http_validation_without_discovery_mutation_endpoints(self):
    self.product('auth')
    self.assertEqual(self.client.get('/catalog/discovery').status_code, 401)
    self.assertEqual(self.client.get('/catalog/discovery/auth').status_code, 401)
    self.assertEqual(self.client.get('/catalog/discovery', headers={'Authorization': 'Bearer invalid'}).status_code, 401)
    unknown = {'Authorization': 'Bearer ' + create_access_token('missing-user')}
    self.assertEqual(self.client.get('/catalog/discovery', headers=unknown).status_code, 401)
    for params in ({'limit': 0}, {'limit': 41}, {'category': 'kids'},
                   {'min_price': -1}, {'min_price': 2000, 'max_price': 1000}):
      with self.subTest(params=params):
        self.assertEqual(self.get(**params).status_code, 422)
    for operation in ('like', 'save'):
      self.assertEqual(self.client.post(f'/catalog/discovery/auth/{operation}', headers=self.headers).status_code, 404)

  def test_feature_status_advertises_only_capability_without_completing_style_swipe(self):
    response = self.client.get('/onboarding/status', headers=self.headers)
    self.assertEqual(response.status_code, 200, response.text)
    self.assertIn('catalogue_discovery_v1', response.json()['recommendation_features'])
    self.assertFalse(response.json()['completed'])
    self.assertEqual(self.client.get('/feed', headers=self.headers).status_code, 409)


if __name__ == '__main__':
  unittest.main()
