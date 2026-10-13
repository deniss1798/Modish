from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models import Product, User
from app.services.product_eligibility import evaluate_eligibility
from app.services.fit_size_profile import update_size_profile
from app.services import recommendation_engine as engine
from fit_fixtures import confirmed_fit


class ExactEligibilityTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.addCleanup(self.db.close)
        self.user = User(id=str(uuid4()), email='exact@example.test', password_hash='unused')
        self.fit = confirmed_fit(id=str(uuid4()), user_id=self.user.id, height_cm=170,
            clothing_size='48', style_constraints={'clothing_size_system': 'RU'},
            gender_target='womenswear', budget_max=7000, shoe_size='38', shoe_system='EU')
        self.db.add_all([self.user, self.fit]); self.db.flush()

    def product(self, **changes):
        pid = str(uuid4())
        fields = dict(id=pid, external_id=pid, source='baon', brand='BAON', title='Брюки прямые',
            category='брюки', gender_target='womenswear', price=5000, currency='RUB',
            available_sizes=['48'], feed_raw_json={'size_system': 'RU'},
            image_url='https://baon.ru/'+pid+'.jpg', product_url='https://baon.ru/'+pid,
            is_active=1, is_available=1, is_deleted_from_feed=0, availability_status='in_stock',
            last_seen_in_feed_at=datetime.now(timezone.utc))
        fields.update(changes)
        product = Product(**fields); self.db.add(product); self.db.flush()
        return product

    def test_size_stock_and_exact_budget_are_hard_constraints(self):
        self.assertTrue(evaluate_eligibility(self.product(price=7000), self.fit).eligible)
        for changes in [dict(available_sizes=['42', '44', '46']), dict(feed_raw_json={}),
                dict(feed_raw_json={'size_system': 'EU'}), dict(available_sizes=[]),
                dict(availability_status=None), dict(is_available=0), dict(price=7001),
                dict(last_seen_in_feed_at=datetime.now(timezone.utc)-timedelta(days=4)),
                dict(gender_target='menswear', title='Брюки мужские')]:
            with self.subTest(changes=changes):
                self.assertFalse(evaluate_eligibility(self.product(**changes), self.fit).eligible)

    def test_invalid_products_never_reach_personal_ranking_even_after_large_prefix(self):
        for _ in range(310):
            self.product(available_sizes=['44'])
        wanted = self.product(updated_at=datetime.now(timezone.utc)-timedelta(days=90))
        ranked = []
        original = engine.score_product
        def score(db, user, product, ctx=None):
            ranked.append(product.id)
            self.assertEqual(product.id, wanted.id)
            return original(db, user, product, ctx)
        with patch.object(engine, 'score_product', side_effect=score):
            rows = engine.generate_feed(self.db, self.user)
        self.assertTrue(ranked)
        self.assertEqual([r.product.id for r in rows], [wanted.id])

    def test_missing_size_never_falls_back_to_legacy_m(self):
        self.fit.clothing_size = 'M'
        self.fit.size_profile_json = {}
        self.assertEqual(evaluate_eligibility(self.product(available_sizes=['M']), self.fit).reason, 'size_unknown')
        self.assertEqual(engine.generate_feed(self.db, self.user), [])

    def test_footwear_uses_its_own_size_not_clothing(self):
        shoes = self.product(title='Ботинки женские', category='обувь', available_sizes=['38'],
            feed_raw_json={'size_system': 'EU'})
        self.assertTrue(evaluate_eligibility(shoes, self.fit).eligible)
        update_size_profile(self.fit, sizes={'shoes': {'value': '39', 'system': 'EU'}})
        self.assertFalse(evaluate_eligibility(shoes, self.fit).eligible)
