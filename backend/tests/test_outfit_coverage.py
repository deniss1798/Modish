"""Complete personal outfits on ten explicit, synthetic size/budget profiles.

These HTTP fixtures prove minimum viable composition on known evidence. They
are not evidence of stock, size contracts or coverage at any real merchant.
Expected admission is a literal SKU ledger, never the production size gate.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import create_access_token, get_db
from app.api.onboarding import router as onboarding_router
from app.api.outfits import router as outfits_router
from app.api.shopping_intents import router as intents_router
from app.db import Base
from app.models import ActivationSession, Product, User


@dataclass(frozen=True)
class CoverageProfile:
    name: str
    top: tuple[str, str]
    bottom: tuple[str, str]
    dress: tuple[str, str]
    shoe: str
    segment: str
    budget: int
    template: str


PROFILES = (
    CoverageProfile('xs-economy', ('XS', 'LETTER'), ('XS', 'LETTER'), ('XS', 'LETTER'), '36', 'economy', 4000, 'separates'),
    CoverageProfile('s-economy', ('S', 'LETTER'), ('S', 'LETTER'), ('S', 'LETTER'), '37', 'economy', 4000, 'dress'),
    CoverageProfile('m-mass', ('M', 'LETTER'), ('M', 'LETTER'), ('M', 'LETTER'), '38', 'mass', 10000, 'separates'),
    CoverageProfile('l-mass', ('L', 'LETTER'), ('L', 'LETTER'), ('L', 'LETTER'), '39', 'mass', 10000, 'dress'),
    CoverageProfile('xl-mid', ('XL', 'LETTER'), ('XL', 'LETTER'), ('XL', 'LETTER'), '40', 'mid', 25000, 'separates'),
    CoverageProfile('xxl-mid', ('XXL', 'LETTER'), ('XXL', 'LETTER'), ('XXL', 'LETTER'), '41', 'mid', 25000, 'dress'),
    CoverageProfile('ru42-mass', ('42', 'RU'), ('42', 'RU'), ('42', 'RU'), '36', 'mass', 10000, 'separates'),
    CoverageProfile('ru46-mid', ('46', 'RU'), ('46', 'RU'), ('46', 'RU'), '39', 'mid', 25000, 'dress'),
    CoverageProfile('mixed-letter-ru', ('M', 'LETTER'), ('48', 'RU'), ('44', 'EU'), '38', 'mass', 10000, 'separates'),
    CoverageProfile('mixed-ru-letter', ('44', 'RU'), ('L', 'LETTER'), ('42', 'EU'), '40', 'mid', 25000, 'separates'),
)


class OutfitCoverageTests(unittest.TestCase):
    @contextmanager
    def catalogue(self, profile):
        engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.sessions = sessionmaker(bind=engine, autoflush=False)
        self.profile = profile
        self.user_id = 'coverage-' + profile.name
        self.ledger = {}
        self.observed_at = datetime.now(timezone.utc)
        try:
            with self.sessions() as db:
                db.add(User(id=self.user_id, email=self.user_id + '@example.test', password_hash='unused'))
                db.flush()
                self._seed(db)
                db.commit()
            app = FastAPI()
            app.include_router(onboarding_router)
            app.include_router(outfits_router)
            app.include_router(intents_router)

            def database():
                with self.sessions() as db:
                    yield db

            app.dependency_overrides[get_db] = database
            self.headers = {'Authorization': 'Bearer ' + create_access_token(self.user_id)}
            with TestClient(app) as client:
                self.client = client
                yield
        finally:
            engine.dispose()

    def expected_size(self, slot):
        return {'top': self.profile.top, 'bottom': self.profile.bottom,
            'one_piece': self.profile.dress, 'shoes': (self.profile.shoe, 'EU')}[slot]

    def _seed(self, db):
        # Six clothing categories allow a genuine 15-card Style Swipe. The
        # dress fixture later observes the separates going out of stock, so
        # both independent templates are required by different profiles.
        categories = [('top', 'рубашки', 'Рубашка хлопковая'),
            ('top', 'футболки', 'Футболка базовая'),
            ('top', 'джемперы', 'Джемпер однотонный'),
            ('bottom', 'брюки', 'Брюки прямые'),
            ('bottom', 'джинсы', 'Джинсы прямые'),
            ('bottom', 'юбки', 'Юбка миди'),
            ('shoes', 'обувь', 'Лоферы кожаные')]
        if self.profile.template == 'dress':
            categories.append(('one_piece', 'платья', 'Платье однотонное'))
        for category_index, (slot, category, title) in enumerate(categories):
            size, system = self.expected_size(slot)
            for index in range(6):
                sku = f'known-{category_index}-{index}'
                price = Decimal('550.25') + category_index * 30 + index * 10
                self._product(db, sku, slot, category, title, size, system, price)
        # Attractive, cheaper counterexamples coexist with the positive pool.
        # Their independent labels ensure empty output is not the only oracle.
        self._product(db, 'reject-wrong-shoe', 'shoes', 'обувь', 'Лоферы кожаные',
            '35' if self.profile.shoe != '35' else '42', 'EU', Decimal('300.01'), admitted=False)
        self._product(db, 'reject-unknown-shoe', 'shoes', 'обувь', 'Лоферы кожаные',
            self.profile.shoe, None, Decimal('300.02'), admitted=False)
        stale = self._product(db, 'reject-stale-shoe', 'shoes', 'обувь', 'Лоферы кожаные',
            self.profile.shoe, 'EU', Decimal('300.03'), admitted=False)
        stale.last_seen_in_feed_at = self.observed_at - timedelta(days=4)

    def _product(self, db, sku, slot, category, title, size, system, price, *, admitted=True):
        raw = {'size': size}
        if system:
            raw['size_system'] = system
        product = Product(id=sku, external_id=sku, source='baon', brand=f'Fixture {sku}',
            title=f'{title} {sku}', category=category, price=price, currency='RUB',
            gender_target='womenswear', available_sizes=[size], feed_raw_json=raw,
            colors=['black'], style_tags=['casual', 'minimalism', 'elegant'],
            image_url=f'https://baon.ru/images/{sku}.jpg', product_url=f'https://baon.ru/products/{sku}',
            is_active=1, is_available=1, availability_status='in_stock', is_deleted_from_feed=0,
            last_seen_in_feed_at=self.observed_at)
        db.add(product)
        self.ledger[sku] = {'slot': slot, 'size': size, 'system': system, 'price': price,
            'admitted': admitted, 'available': True}
        return product

    def request(self, method, path, **options):
        response = self.client.request(method, path, headers=self.headers, **options)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def warmup(self):
        top_value, top_system = self.profile.top
        self.request('POST', '/onboarding/preferences', json={
            'gender_target': 'womenswear', 'age_group': '25-34', 'height': 170,
            'clothing_size': top_value, 'clothing_size_system': top_system,
            'shoe_size': self.profile.shoe, 'shoe_size_system': 'EU',
            'styles': ['casual', 'minimalism'], 'price_segment': self.profile.segment,
            'sizes': {slot: {'value': value, 'system': system} for slot, (value, system) in (
                ('top', self.profile.top), ('bottom', self.profile.bottom),
                ('one_piece', self.profile.dress), ('shoes', (self.profile.shoe, 'EU')))},
        })
        deck = self.request('GET', '/onboarding/style-swipe')
        self.assertGreaterEqual(len(deck['products']), 15,
            'A real, nonempty Style Swipe must precede every coverage result')
        self.assertEqual(len({row['id'] for row in deck['products']}), len(deck['products']))
        for product in deck['products']:
            self.assert_product(product, self.ledger[product['id']]['slot'])
        for product in deck['products'][:15]:
            accepted = self.request('POST', '/onboarding/style-swipe',
                json={'product_id': product['id'], 'reaction': 'like'})
        self.assertEqual(accepted['rated'], 15)
        self.assertTrue(accepted['can_continue'])
        if self.profile.template == 'dress':
            # This is an explicit stock observation between the user's swipe
            # and WOW, not a mocked ranking or a weakened admission rule.
            with self.sessions() as db:
                for product in db.scalars(select(Product)):
                    if self.ledger[product.id]['slot'] in {'top', 'bottom'}:
                        product.is_available, product.availability_status = 0, 'out_of_stock'
                        self.ledger[product.id]['available'] = False
                db.commit()

    def assert_product(self, product, slot):
        sku = product['id']
        self.assertIn(sku, self.ledger)
        expected = self.ledger[sku]
        self.assertTrue(expected['admitted'], f'Counterexample leaked: {sku}')
        self.assertTrue(expected['available'], f'Unavailable fixture leaked: {sku}')
        self.assertEqual(expected['slot'], slot)
        self.assertEqual((expected['size'], expected['system']), self.expected_size(slot))
        self.assertEqual(product['available_sizes'], [expected['size']])
        self.assertEqual(Decimal(str(product['price'])), expected['price'])
        self.assertEqual(product['gender_target'], 'womenswear')
        self.assertEqual(product['currency'], 'RUB')
        self.assertTrue(product['is_available'])
        self.assertEqual(product['availability_status'], 'in_stock')
        self.assertFalse(product['is_deleted_from_feed'])
        self.assertTrue(product['image_url'].startswith('https://baon.ru/images/'))
        self.assertTrue(product['product_url'].startswith('https://baon.ru/products/'))

    def assert_outfits(self, outfits, *, intent=False, clothing_base=False):
        self.assertGreaterEqual(len(outfits), 1, 'A positive catalogue must produce a complete outfit')
        self.assertLessEqual(len(outfits), 3)
        used = set()
        previous = []
        expected_slots = {'one_piece', 'shoes'} if self.profile.template == 'dress' else {'top', 'bottom', 'shoes'}
        if clothing_base:
            expected_slots.discard('shoes')
        for outfit in outfits:
            if clothing_base:
                self.assertEqual(outfit['completeness'], 'clothing_base')
                self.assertEqual(outfit['missing_slots'], ['shoes'])
                self.assertIn('Обувь не подобрана', outfit['completion_message'])
            self.assertEqual(set(outfit['items']), expected_slots)
            self.assertEqual(set(outfit['products']), expected_slots)
            ids = set(outfit['items'].values())
            self.assertEqual(len(ids), len(expected_slots))
            if intent:
                self.assertTrue(all(len(ids - old) >= 2 for old in previous),
                    'Intent alternatives must change at least two garments')
            else:
                self.assertTrue(ids.isdisjoint(used), 'A new short batch must diversify its garments')
            previous.append(ids)
            used.update(ids)
            total = Decimal(0)
            for slot, sku in outfit['items'].items():
                self.assertEqual(outfit['products'][slot]['id'], sku)
                self.assert_product(outfit['products'][slot], slot)
                total += self.ledger[sku]['price']
                if intent:
                    match = outfit['size_matches'][slot]
                    self.assertEqual((match['normalized_size'], match['size_system']), self.expected_size(slot))
                    self.assertEqual(match['availability'], 'available')
            self.assertEqual(Decimal(str(outfit['total_price'])), total)
            self.assertGreater(total, 0)
            self.assertLessEqual(total, Decimal(self.profile.budget))

    def results(self):
        wow = self.request('POST', '/onboarding/complete')
        self.assertTrue(wow['completed'])
        generated = self.request('POST', '/outfits/generate', params={'count': 3, 'scenario': 'daily'})
        text = f'Образ до {self.profile.budget}' + (' без платья' if self.profile.template == 'separates' else '')
        intent = self.request('POST', '/shopping-intents', json={'text': text, 'request_id': 'coverage-intent-request'})
        return wow, generated, intent

    def positive(self, profile):
        with self.catalogue(profile):
            self.warmup()
            wow, generated, intent = self.results()
            self.assert_outfits(wow['outfits'])
            self.assertIsNone(wow['message'])
            self.assert_outfits(generated)
            self.assert_outfits(intent['outfits'], intent=True)
            self.assertEqual(intent['products'], [])
            with self.sessions() as db:
                activation = db.get(ActivationSession, self.user_id)
                self.assertIsNotNone(activation.completed_at)
                self.assertEqual(len(activation.swipes_json), 15)

    def negative(self, defect):
        with self.catalogue(PROFILES[2]):
            self.warmup()
            with self.sessions() as db:
                for product in db.scalars(select(Product)):
                    label = self.ledger[product.id]
                    if defect == 'whole_budget':
                        product.price = Decimal('6000.01')
                        label['price'] = product.price
                    elif defect == 'out_of_stock_bottom' and label['slot'] == 'bottom':
                        product.is_available, product.availability_status = 0, 'out_of_stock'
                        label['available'] = False
                    elif label['slot'] == 'shoes':
                        if defect == 'unknown_shoe_units':
                            product.feed_raw_json = {'size': self.profile.shoe}
                            product.size_system = None
                        elif defect == 'wrong_shoe_size':
                            product.available_sizes = ['42']
                            product.feed_raw_json = {'size': '42', 'size_system': 'EU'}
                db.commit()
            wow, generated, intent = self.results()
            if defect in ('unknown_shoe_units', 'wrong_shoe_size'):
                self.assert_outfits(wow['outfits'], clothing_base=True)
                self.assert_outfits(generated, clothing_base=True)
                self.assertIsNone(wow['message'])
            else:
                self.assertEqual(wow['outfits'], [])
                self.assertEqual(generated, [])
                self.assertEqual(wow['message'], 'Пока нет подходящих образов. Попробуйте изменить предпочтения или посмотреть каталог.')
            self.assertEqual(intent['outfits'], [])
            self.assertEqual(intent['products'], [])
            # "ready" describes the parsed task, not catalogue availability.
            # The existing API conveys empty outfits through its explicit copy.
            self.assertEqual(intent['state'], 'ready')
            self.assertEqual(intent['message'], 'Пока нет подходящих образов. Попробуйте изменить предпочтения или посмотреть каталог.')


def positive_case(profile):
    def test(self):
        self.positive(profile)
    return test


def negative_case(defect):
    def test(self):
        self.negative(defect)
    return test


for profile in PROFILES:
    setattr(OutfitCoverageTests, 'test_complete_session_' + profile.name.replace('-', '_'), positive_case(profile))
for defect in ('unknown_shoe_units', 'wrong_shoe_size', 'out_of_stock_bottom', 'whole_budget'):
    setattr(OutfitCoverageTests, 'test_honest_empty_' + defect, negative_case(defect))


if __name__ == '__main__':
    unittest.main()
