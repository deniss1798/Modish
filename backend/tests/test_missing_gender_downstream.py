"""An explicit gender choice is required before any personal catalogue result."""
from datetime import datetime, timezone
import unittest

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db import Base
from app.models import ActivationSession, FitProfile, Outfit, Product, Recommendation, StyleProfile, User
from app.services.catalog.rule_filters import product_gender_compatible
from app.services.catalog_discovery import DiscoveryError, catalogue_detail, catalogue_page
from app.services.feed_filters import product_passes_hard_filters
from app.services.intent_outfits import build_intent_outfits
from app.services.mie_scoring import compute_fit_score
from app.services.outfit_engine_v2 import generate_outfits_v2
from app.services.outfit_service import generate_outfits
from app.services.product_complements import complementary_products
from app.services.product_eligibility import evaluate_eligibility
from app.services.recommendation_engine import generate_feed
from app.services.recommendation_service import generate_recommendations
from app.services.shopping_intent_parser import parse_intent
from app.services.shopping_intent_service import item_results
from fit_fixtures import confirmed_fit


class MissingGenderDownstreamTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.addCleanup(self.db.close)
        self.user = User(id='missing-gender-user', email='missing-gender@example.test', password_hash='unused')
        self.fit = confirmed_fit(id='missing-gender-fit', user_id=self.user.id, height_cm=170,
            gender_target='womenswear', clothing_size='M', budget_max=10000,
            shoe_size='38', shoe_system='EU')
        self.profile = StyleProfile(id='missing-gender-style', user_id=self.user.id,
            style_target='unknown', profile_json={})
        self.db.add_all([self.user, self.fit, self.profile])
        self.products = []
        for gender in ('womenswear', 'menswear', 'unisex'):
            for role, title, category in (
                    ('top', 'Рубашка хлопковая', 'рубашки'),
                    ('bottom', 'Брюки прямые', 'брюки'),
                    ('shoes', 'Кеды кожаные', 'обувь')):
                pid = f'{gender}-{role}'
                product = Product(id=pid, external_id=pid, source='baon', brand='BAON',
                    title=title, category=category, gender_target=gender, price=1500, currency='RUB',
                    available_sizes=['38'] if role == 'shoes' else ['M'],
                    feed_raw_json={'size_system': 'EU' if role == 'shoes' else 'INT'},
                    colors=['black'], color_family='black', style_tags=['casual'],
                    image_url=f'https://catalog.example.test/{pid}.jpg',
                    product_url=f'https://catalog.example.test/products/{pid}',
                    is_active=1, is_available=1, is_deleted_from_feed=0,
                    availability_status='in_stock', last_seen_in_feed_at=datetime.now(timezone.utc))
                self.products.append(product)
                self.db.add(product)
        self.db.flush()
        self.fit.gender_target = None
        self.db.flush()
        self.db.expire(self.fit)
        self.assertIsNone(self.db.scalar(select(FitProfile.gender_target).where(FitProfile.id == self.fit.id)))

    def test_null_gender_rejects_known_sized_catalogue_before_ranking(self):
        for product in self.products:
            with self.subTest(product=product.id):
                self.assertFalse(product_gender_compatible(product, None))
                self.assertFalse(product_passes_hard_filters(product, self.fit))
                self.assertEqual(evaluate_eligibility(product, self.fit).reason, 'fit_profile_unknown')
                score, rejected, _, debug = compute_fit_score(product, fit=self.fit,
                    palette=set(), avoid_colors=set())
                self.assertEqual(score, 0)
                self.assertTrue(rejected)
                self.assertEqual(debug['fit_gender'], 0)
        self.assertEqual(generate_feed(self.db, self.user), [])

    def test_empty_unknown_or_absent_profile_never_means_unisex(self):
        for gender in ('', ' ', 'unknown', 'invalid'):
            with self.subTest(gender=gender):
                self.fit.gender_target = gender
                self.db.flush()
                self.assertFalse(product_gender_compatible(self.products[0], gender))
                self.assertEqual(generate_feed(self.db, self.user), [])
                self.assertEqual(catalogue_page(self.db, self.user)['products'], [])
        self.db.delete(self.fit)
        self.db.flush()
        self.assertEqual(generate_feed(self.db, self.user), [])
        self.assertEqual(generate_outfits(self.db, self.user), [])
        self.assertEqual(catalogue_page(self.db, self.user)['products'], [])

    def test_completed_activation_cannot_bypass_null_gender(self):
        self.db.add(ActivationSession(user_id=self.user.id, completed_at=datetime.now(timezone.utc)))
        self.db.flush()
        self.assertEqual(generate_feed(self.db, self.user), [])
        self.assertEqual(generate_outfits_v2(self.db, self.user), [])

    def test_null_gender_blocks_outfits_complements_and_legacy_text_recommendations(self):
        self.assertEqual(generate_outfits(self.db, self.user), [])
        self.assertEqual(generate_outfits(self.db, self.user, use_v2=False), [])
        self.assertEqual(complementary_products(self.db, self.user, self.products[0]), [])
        self.assertEqual(generate_recommendations(self.db, self.profile, count=1, scenario='daily'), [])
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Outfit)), 0)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Recommendation)), 0)
        self.assertIsNone(self.fit.gender_target)

    def test_null_gender_blocks_item_and_outfit_intent_results(self):
        self.assertEqual(item_results(self.db, self.user, parse_intent('Брюки до 7000')), [])
        self.assertEqual(build_intent_outfits(self.db, self.user, parse_intent('Собери образ до 10000'),
            intent_id='missing-gender-intent', version=1), [])
        self.assertIsNone(self.fit.gender_target)

    def test_discovery_reports_null_scope_and_never_browses_unisex_as_a_default(self):
        page = catalogue_page(self.db, self.user)
        self.assertEqual(page['products'], [])
        self.assertIsNone(page['next_cursor'])
        self.assertFalse(page['has_more'])
        self.assertEqual(page['scope']['gender_basis'], 'missing')
        self.assertIsNone(page['scope']['gender_target'])
        for product in self.products:
            with self.subTest(product=product.id), self.assertRaises(DiscoveryError) as error:
                catalogue_detail(self.db, self.user, product.id)
            self.assertEqual(error.exception.status, 404)
        self.assertIsNone(self.fit.gender_target)

    def test_explicit_women_men_and_unisex_choices_keep_results(self):
        for gender in ('womenswear', 'menswear', 'unisex'):
            with self.subTest(gender=gender):
                self.fit.gender_target = gender
                self.db.flush()
                allowed = {'womenswear', 'menswear', 'unisex'} if gender == 'unisex' else {gender, 'unisex'}
                for product in self.products:
                    self.assertEqual(evaluate_eligibility(product, self.fit).eligible,
                        product.gender_target in allowed)
                feed = generate_feed(self.db, self.user)
                self.assertTrue(feed)
                self.assertTrue(all(item.product.gender_target in allowed for item in feed))
                page = catalogue_page(self.db, self.user)
                self.assertEqual(page['scope']['gender_target'], gender)
                self.assertEqual(page['scope']['gender_basis'], 'profile')
                self.assertEqual({row['gender_target'] for row in page['products']}, allowed)
                self.assertTrue(item_results(self.db, self.user, parse_intent('Брюки до 7000')))
                self.assertTrue(build_intent_outfits(self.db, self.user, parse_intent('Собери образ до 10000'),
                    intent_id=f'intent-{gender}', version=1))
                self.assertTrue(generate_outfits_v2(self.db, self.user, replace_existing=False))


if __name__ == '__main__':
    unittest.main()
