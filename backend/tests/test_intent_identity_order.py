"""Offer insertion/query-plan order must not choose an arbitrary visible alias."""
from contextlib import contextmanager
from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models import Product, User
from app.services import activation_service as activation
from app.services import intent_outfits, shopping_intent_service as intents
from app.services.shopping_intent_parser import parse_intent


def product_fields(pid, *, role='top', **changes):
  titles = {'top': 'Рубашка однотонная', 'bottom': 'Брюки прямые', 'shoes': 'Лоферы кожаные'}
  categories = {'top': 'рубашки', 'bottom': 'брюки', 'shoes': 'обувь'}
  fields = dict(id=pid, external_id=pid, source='baon', brand='BAON',
      title=titles[role], category=categories[role], gender_target='womenswear',
      price=1500, currency='RUB', available_sizes=['38'] if role == 'shoes' else ['M'],
      feed_raw_json={'size_system': 'EU' if role == 'shoes' else 'LETTER'},
      colors=['black'], style_tags=['casual', 'minimalism'],
      image_url=f'https://catalog.example.test/{pid}.jpg',
      product_url=f'https://catalog.example.test/p/{pid}',
      is_active=1, is_available=1, availability_status='in_stock', is_deleted_from_feed=0,
      last_seen_in_feed_at=datetime.now(timezone.utc))
  fields.update(changes)
  return fields


@contextmanager
def catalogue(rows):
  engine = create_engine('sqlite:///:memory:')
  Base.metadata.create_all(engine)
  try:
    with Session(engine, autoflush=False) as db:
      user = User(id='identity-order-user', email='order@example.test', password_hash='unused')
      db.add(user)
      db.flush()
      activation.configure(db, user, gender_target='womenswear', age_group='25-34', height=170,
          clothing_size='M', clothing_size_system='LETTER', shoe_size='38', shoe_size_system='EU',
          styles=['casual', 'minimalism'], price_segment='mass')
      for fields in rows:
        db.add(Product(**fields))
        db.flush()  # Explicitly control insertion order, not primary-key order.
      db.commit()
      yield db, user
  finally:
    engine.dispose()


class IntentIdentityOrderTests(unittest.TestCase):
  def aliases(self, *, rejected_first=False):
    common = {'image_url': 'https://catalog.example.test/shared-model.jpg'}
    return [product_fields('a-canonical', **common,
                           available_sizes=['L'] if rejected_first else ['M']),
            product_fields('z-preferred', **common)]

  def test_single_item_uses_stable_first_eligible_id_in_both_insertion_orders(self):
    rows = self.aliases()
    for inserted in (rows, list(reversed(rows))):
      with self.subTest(order=[row['id'] for row in inserted]), catalogue(inserted) as (db, user):
        scored = []
        def score(product):
          scored.append(product.id)
          return .9 if product.id == 'z-preferred' else .1
        with patch.object(intents.AffinityScorer, 'score', side_effect=score):
          results = intents.item_results(db, user, parse_intent('Рубашка до 7000'))
        self.assertEqual([row['product']['id'] for row in results], ['a-canonical'])
        self.assertEqual(scored, ['a-canonical'])
        # An explicitly excluded offer still suppresses every visible alias.
        self.assertEqual(intents.item_results(db, user, parse_intent('Рубашка до 7000'),
            excluded_ids=['z-preferred']), [])

  def test_rejected_lower_id_cannot_reserve_identity_before_admission(self):
    rows = self.aliases(rejected_first=True)
    for inserted in (rows, list(reversed(rows))):
      with self.subTest(order=[row['id'] for row in inserted]), catalogue(inserted) as (db, user):
        results = intents.item_results(db, user, parse_intent('Рубашка до 7000'))
        self.assertEqual([row['product']['id'] for row in results], ['z-preferred'])

  def test_outfit_role_hydration_keeps_the_same_canonical_top_in_both_orders(self):
    rows = self.aliases() + [product_fields('b-bottom', role='bottom'),
                            product_fields('c-shoes', role='shoes')]
    expected = {'top': 'a-canonical', 'bottom': 'b-bottom', 'shoes': 'c-shoes'}
    outfit_ids = []
    for inserted in (rows, list(reversed(rows))):
      with self.subTest(order=[row['id'] for row in inserted]), catalogue(inserted) as (db, user):
        with patch.object(intent_outfits.AffinityScorer, 'score',
                side_effect=lambda p: .9 if p.id == 'z-preferred' else .1):
          results = intent_outfits.build_intent_outfits(db, user,
              parse_intent('Образ на каждый день до 10000'), intent_id='fixed-intent', version=1)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['items'], expected)
        self.assertEqual(len(results[0]['size_matches']), 3)
        outfit_ids.append(results[0]['id'])
    self.assertEqual(len(set(outfit_ids)), 1)


if __name__ == '__main__':
  unittest.main()
