"""Legacy malformed image identities cannot break an otherwise valid feed."""
from datetime import datetime, timezone
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models import Product, User
from app.services import activation_service as activation
from app.services.catalog.quality_gate import evaluate_product
from app.services.product_eligibility import evaluate_eligibility
from app.services.product_identity import identity_url, product_identity_keys
from app.services.shopping_intent_parser import parse_intent
from app.services.shopping_intent_service import item_results


class ProductIdentityResilienceTests(unittest.TestCase):
  def setUp(self):
    self.engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(self.engine)
    self.db = Session(self.engine, autoflush=False)
    self.addCleanup(self.engine.dispose)
    self.addCleanup(self.db.close)
    self.user = User(id='identity-user', email='identity@example.test', password_hash='unused')
    self.db.add(self.user)
    self.db.flush()
    self.session = activation.configure(self.db, self.user, age_group='25-34', height=170,
        clothing_size='M', clothing_size_system='LETTER', shoe_size='38', shoe_size_system='EU',
        styles=['casual', 'minimalism'], price_segment='mass')

  def product(self, pid, *, shoe=False, **changes):
    fields = dict(id=pid, external_id=pid, source='baon', brand='BAON',
        title='Лоферы кожаные' if shoe else 'Рубашка', category='обувь' if shoe else 'рубашки',
        price=1500, currency='RUB', gender_target='womenswear',
        available_sizes=['38'] if shoe else ['M'],
        feed_raw_json={'size_system': 'EU' if shoe else 'LETTER'},
        colors=['black'], style_tags=['casual', 'minimalism'],
        image_url=f'https://catalog.example.test/{pid}.jpg',
        product_url=f'https://catalog.example.test/p/{pid}',
        is_active=1, is_available=1, availability_status='in_stock', is_deleted_from_feed=0,
        last_seen_in_feed_at=datetime.now(timezone.utc))
    fields.update(changes)
    product = Product(**fields)
    self.db.add(product)
    self.db.flush()
    return product

  def test_valid_cdn_urls_keep_existing_identity_contract(self):
    cases = {
        'https://CDN.example.test/model.jpg?width=300&utm_source=x': 'cdn.example.test/model.jpg',
        '//cdn.example.test/model.jpg?width=600': 'cdn.example.test/model.jpg',
        'https://cdn.example.test/image?model=1': 'cdn.example.test/image?model=1',
        'https://cdn.example.test/image?model=2': 'cdn.example.test/image?model=2',
        'https://cdn.example.test': 'https://cdn.example.test',
        '  local.jpg  ': 'local.jpg',
        '': '',
    }
    for raw, expected in cases.items():
      with self.subTest(raw=raw):
        self.assertEqual(identity_url(raw), expected)

  def test_malformed_url_is_opaque_identity_and_remains_quarantined(self):
    product = self.product('legacy-invalid', image_url='https://[broken')
    self.assertIn('image:https://[broken', product_identity_keys(product))
    self.assertEqual(identity_url('  https://[broken  '), 'https://[broken')
    quality = evaluate_product(product)
    self.assertEqual(quality.status, 'quarantined')
    self.assertIn('invalid_image_url', quality.reasons)
    self.assertEqual(activation.candidate_pool(self.db, self.user, self.session), [])

  def test_invalid_hidden_row_cannot_abort_full_or_mandatory_role_pool(self):
    hidden = self.product('hidden-invalid', image_url='https://[broken')
    good = self.product('valid-shoe', shoe=True)
    self.db.commit()
    # Run the full ORM hidden-row path and the thin mandatory-role identity
    # projection. Both must retain exclusion and independently admit the shoe.
    for roles in ((), {'shoes'}):
      with self.subTest(required_roles=roles):
        rows = activation.candidate_pool(self.db, self.user, self.session,
            exclude_ids=[hidden.id], required_roles=roles)
        self.assertEqual([row.id for row in rows], [good.id])

  def test_valid_hidden_cdn_alias_is_still_excluded(self):
    hidden = self.product('hidden-valid', image_url='https://catalog.example.test/shared.jpg?width=300')
    self.product('same-model', shoe=True, image_url='https://catalog.example.test/shared.jpg?width=600')
    self.db.commit()
    for roles in ((), {'shoes'}):
      with self.subTest(required_roles=roles):
        self.assertEqual(activation.candidate_pool(self.db, self.user, self.session,
            exclude_ids=[hidden.id], required_roles=roles), [])

  def test_intent_rejects_malformed_or_private_images_before_ranking(self):
    valid = self.product('valid-intent-photo')
    invalid_urls = ('https://[broken', 'http://127.0.0.1/photo.jpg',
        'http://localhost/photo.jpg', '//catalog.example.test/photo.jpg',
        'data:image/png;base64,AAAA', 'javascript:alert(1)')
    invalid = [self.product(f'bad-intent-photo-{index}', image_url=url)
               for index, url in enumerate(invalid_urls)]
    fit = activation._preferences(self.db, self.user)[1]
    for product in invalid:
      with self.subTest(image_url=product.image_url):
        decision = evaluate_eligibility(product, fit)
        self.assertFalse(decision.eligible)
        self.assertEqual(decision.reason, 'catalog_quality')
    results = item_results(self.db, self.user, parse_intent('Рубашка до 7000'))
    self.assertEqual([row['product']['id'] for row in results], [valid.id])


if __name__ == '__main__':
  unittest.main()
