"""Legacy normal, refill and emergency paths never relax exact Fit sizes."""
from datetime import datetime, timezone
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models import Product, User
from app.services import outfit_service
from app.services.product_eligibility import evaluate_eligibility
from fit_fixtures import confirmed_fit


class LegacyOutfitExactSizeTests(TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.addCleanup(self.db.close)
        self.user = User(id=str(uuid4()), email='legacy-size@example.test', password_hash='unused')
        self.fit = confirmed_fit(id=str(uuid4()), user_id=self.user.id,
            height_cm=170, gender_target='womenswear', clothing_size='M',
            budget_max=10000, shoe_size='38', shoe_system='EU')
        self.db.add_all([self.user, self.fit])
        self.db.flush()

    def product(self, slot, size, *, system=None, details=None):
        title, category = {
            'top': ('Рубашка женская', 'рубашки'),
            'bottom': ('Брюки женские прямые', 'брюки'),
            'shoes': ('Ботинки женские', 'обувь'),
        }[slot]
        pid = str(uuid4())
        now = datetime.now(timezone.utc)
        product = Product(id=pid, external_id=pid, source='baon', brand='BAON',
            title=title, category=category, gender_target='womenswear', price=2000,
            currency='RUB', available_sizes=[size],
            available_sizes_detailed=details or [],
            feed_raw_json={'size_system': system} if system else {},
            image_url=f'https://baon.ru/{pid}.jpg', product_url=f'https://baon.ru/{pid}',
            is_active=1, is_available=1, is_deleted_from_feed=0,
            availability_status='in_stock', last_seen_in_feed_at=now,
            created_at=now, updated_at=now)
        self.db.add(product)
        self.db.flush()
        return product

    def complete_catalogue(self, clothing_size='M', *, system=None):
        return [self.product('top', clothing_size, system=system),
            self.product('bottom', clothing_size, system=system),
            self.product('shoes', '38', system='EU')]

    def generate_emergency(self):
        # Current legacy retrieval is the catalogue helper, not generate_feed.
        # Empty both strict admission stages so the raw emergency query runs.
        with patch.object(outfit_service, '_catalog_products_for_outfits', return_value=[]), \
                patch.object(outfit_service, '_extend_buckets'), \
                patch.object(outfit_service, '_bucket_products', wraps=outfit_service._bucket_products) as bucket:
            result = outfit_service.generate_outfits(self.db, self.user, count=1, use_v2=False)
        self.assertEqual(bucket.call_count, 2)
        self.assertEqual(bucket.call_args_list[0].args[0], [])
        self.assertTrue(bucket.call_args_list[1].args[0], 'Raw emergency catalogue must be exercised')
        return result

    def assert_complete_exact_outfit(self, rows, products):
        self.assertEqual(len(rows), 1)
        self.assertEqual(set(rows[0].items_json), {'top', 'bottom', 'shoes'})
        self.assertEqual(set(rows[0].items_json.values()), {p.id for p in products})
        for pid in rows[0].items_json.values():
            self.assertTrue(evaluate_eligibility(self.db.get(Product, pid), self.fit).eligible)

    def test_normal_path_keeps_correct_clothing_and_independent_shoe_size(self):
        products = self.complete_catalogue()
        rows = outfit_service.generate_outfits(self.db, self.user, count=1, use_v2=False)
        self.assert_complete_exact_outfit(rows, products)

    def test_emergency_path_keeps_correct_clothing_and_independent_shoe_size(self):
        products = self.complete_catalogue()
        self.assert_complete_exact_outfit(self.generate_emergency(), products)

    def test_letter_mismatches_never_enter_emergency_outfit(self):
        from app.services.fit_size_profile import submitted_sizes, update_size_profile
        for requested, offered in [('S', 'XL'), ('L', 'M')]:
            with self.subTest(requested=requested, offered=offered):
                update_size_profile(self.fit, sizes=submitted_sizes(clothing_size=requested))
                products = self.complete_catalogue(offered)
                self.assertEqual(self.generate_emergency(), [])
                for product in products:
                    self.db.delete(product)
                self.db.flush()

    def test_mixed_emergency_catalogue_keeps_only_exact_size_products(self):
        self.product('top', 'XL')
        self.product('bottom', 'L')
        self.product('shoes', '39', system='EU')
        products = self.complete_catalogue()
        self.assert_complete_exact_outfit(self.generate_emergency(), products)

    def test_numeric_mismatch_is_not_an_ordered_size_comparison(self):
        from app.services.fit_size_profile import submitted_sizes, update_size_profile
        update_size_profile(self.fit, sizes=submitted_sizes(clothing_size='46', clothing_system='RU'))
        self.complete_catalogue('48', system='RU')
        self.assertEqual(self.generate_emergency(), [])

    def test_confirmed_numeric_match_keeps_correct_outfit(self):
        from app.services.fit_size_profile import submitted_sizes, update_size_profile
        update_size_profile(self.fit, sizes=submitted_sizes(clothing_size='46', clothing_system='RU'))
        products = self.complete_catalogue('46', system='RU')
        self.assert_complete_exact_outfit(self.generate_emergency(), products)

    def test_equal_numeric_tokens_with_other_or_unknown_system_do_not_match(self):
        from app.services.fit_size_profile import submitted_sizes, update_size_profile
        update_size_profile(self.fit, sizes=submitted_sizes(clothing_size='46', clothing_system='RU'))
        for system in ('EU', None):
            with self.subTest(system=system):
                products = self.complete_catalogue('46', system=system)
                self.assertEqual(self.generate_emergency(), [])
                for product in products:
                    self.db.delete(product)
                self.db.flush()

    def test_unknown_fit_never_enters_emergency_outfit(self):
        self.complete_catalogue()
        self.db.delete(self.fit)
        self.db.flush()
        self.assertEqual(self.generate_emergency(), [])

    def test_unconfirmed_slot_cannot_use_legacy_clothing_size(self):
        self.complete_catalogue()
        self.fit.size_profile_json = {}
        self.assertEqual(self.generate_emergency(), [])

    def test_refill_applies_exact_gate_to_all_roles(self):
        from app.services.fit_size_profile import submitted_sizes, update_size_profile
        update_size_profile(self.fit, sizes=submitted_sizes(clothing_size='S'))
        invalid = self.complete_catalogue('XL')[:2]
        invalid.append(self.product('shoes', '39', system='EU'))
        correct = [self.product('top', 'S'), self.product('bottom', 'S'),
            self.product('shoes', '38', system='EU')]
        buckets = {slot: [] for slot in ('top', 'bottom', 'shoes', 'accessory')}
        outfit_service._extend_buckets(self.db, buckets, excluded=set(),
            min_per_slot=3, fit=self.fit, budget_max=self.fit.budget_max)
        returned = {p.id for values in buckets.values() for p in values}
        self.assertTrue({p.id for p in correct} <= returned)
        self.assertTrue(returned.isdisjoint(p.id for p in invalid))
        self.assertTrue(all(evaluate_eligibility(p, self.fit).eligible
            for values in buckets.values() for p in values))

    def test_unavailable_exact_size_cannot_reenter_emergency_outfit(self):
        for slot in ('top', 'bottom'):
            self.product(slot, 'M', details=[{'raw_size': 'M', 'availability': 'unavailable'}])
        self.product('shoes', '38', system='EU')
        self.assertEqual(self.generate_emergency(), [])
