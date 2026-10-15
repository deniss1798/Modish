from collections import Counter
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from uuid import uuid4

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from app.db import Base
from app.models import Product, RecommendationEventV2, TasteProfile, User, UserProductState, UserTasteFeature
from app.schemas.product_event import EventRequest
from app.services import activation_service as activation
from app.services.catalog_filters import CatalogFilters
from app.services.personalization_scoring import AffinityScorer
from app.services.product_identity import product_identity_keys
from app.services.product_event_service import record_product_event
from app.services.user_twin_service import DNA_TASTE_PREFIX, apply_event_to_user_twin


class ArchitectureRegressionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine, autoflush=False)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        self.user = User(id=str(uuid4()), email='architecture@example.test', password_hash='unused')
        self.db.add(self.user); self.db.flush()

    def product(self, **changes):
        pid = str(uuid4())
        row = dict(id=pid, external_id=pid, source='baon', title='Рубашка', category='рубашки',
            brand='BAON', price=2500, currency='RUB', colors=['black'], style_tags=['casual'],
            available_sizes=['M'], gender_target='womenswear', is_active=1, is_available=1, availability_status='in_stock',
            is_deleted_from_feed=0, last_seen_in_feed_at=datetime.now(timezone.utc),
            image_url='https://baon.ru/'+pid+'.jpg', product_url='https://baon.ru/p/'+pid,
            feed_raw_json={'size_system': 'LETTER'})
        row.update(changes)
        product = Product(**row); self.db.add(product); self.db.flush()
        return product

    def test_store_filter_applies_before_top_50_cutoff(self):
        session = activation.configure(self.db, self.user, gender_target='womenswear', age_group='25-34', height=170, clothing_size='M', shoe_size='38',
            clothing_size_system='LETTER', shoe_size_system='EU', styles=['casual', 'minimalism'], price_segment='mass')
        session.completed_at = datetime.now(timezone.utc)
        for i in range(60):
            self.product(id=f'a{i:035}')
        target = self.product(id='z'*36, source='shoppinglive')
        rows = activation.personalized_feed(self.db, self.user, session, source='shoppinglive',
            filters=CatalogFilters(categories=('tops',)))
        self.assertEqual([row.product.id for row in rows], [target.id])

    def test_rejected_sizes_do_not_compute_identity_or_hide_an_eligible_alias(self):
        from unittest.mock import patch
        session = activation.configure(self.db, self.user, gender_target='womenswear', age_group='25-34', height=170,
            clothing_size='M', clothing_size_system='LETTER', shoe_size='38', shoe_size_system='EU',
            styles=['casual', 'minimalism'], price_segment='mass')
        for index in range(120):
            self.product(id=f'a{index:035d}', available_sizes=['XL'], group_id='same-offer')
        winner = self.product(id='b'*36, group_id='same-offer')
        alias = self.product(id='c'*36, group_id='same-offer')
        checked = []
        def identity(product):
            checked.append(product.id)
            return product_identity_keys(product)
        with patch('app.services.activation_service.product_identity_keys', side_effect=identity):
            pool = activation.candidate_pool(self.db, self.user, session)
        self.assertEqual([product.id for product in pool], [winner.id])
        self.assertEqual(checked, [winner.id, alias.id])

    def test_feature_writes_are_batched_and_rollback_as_one_transaction(self):
        product = self.product(feed_raw_json={'material': 'cotton', 'fit': 'regular', 'pattern': 'striped',
            'length': 'midi', 'sleeve': 'long', 'rise': 'high', 'texture': 'ribbed'})
        self.db.commit()
        self.db.refresh(self.user); self.db.refresh(product)
        statements = Counter()
        def track(_conn, _cursor, sql, _params, _ctx, _many):
            statements[sql.split()[0]] += 1
        event.listen(self.engine, 'before_cursor_execute', track)
        try:
            touched = apply_event_to_user_twin(self.db, user_id=self.user.id, product=product, event_type='like')
        finally:
            event.remove(self.engine, 'before_cursor_execute', track)
        self.assertGreaterEqual(len(touched), 11)
        self.assertLessEqual(statements['SELECT'], 3)
        self.assertLessEqual(statements['INSERT'], 2)
        self.assertTrue(all(row.positive_count == 1 for row in touched))
        self.db.rollback()
        self.assertEqual(list(self.db.scalars(select(UserTasteFeature))), [])
        self.assertIsNone(self.db.scalar(select(TasteProfile)))

    def test_prefetched_features_do_not_overwrite_a_later_committed_signal(self):
        product = self.product(title='Хлопковая рубашка')
        apply_event_to_user_twin(self.db, user_id=self.user.id, product=product, event_type='like')
        self.db.commit()
        stale = list(self.db.scalars(select(UserTasteFeature)))
        stale_profile = self.db.scalar(select(TasteProfile))
        with Session(self.engine, autoflush=False) as other:
            apply_event_to_user_twin(other, user_id=self.user.id, product=product, event_type='like')
            other.commit()
        self.assertTrue(all(row.positive_count == 1 for row in stale))
        self.assertEqual(stale_profile.profile_version, 2)
        touched = apply_event_to_user_twin(self.db, user_id=self.user.id, product=product, event_type='like')
        self.assertTrue(all(row.positive_count == 3 for row in touched))
        self.assertEqual(stale_profile.profile_version, 4)

    def test_score_cache_is_bounded_and_does_not_cross_user_requests(self):
        profile = SimpleNamespace(style_preferences=[])
        taste = TasteProfile(category_weights={}, brand_weights={}, color_weights={}, style_weights={})
        key = ('material', DNA_TASTE_PREFIX+'cotton')
        first = AffinityScorer(profile, taste, taste_features={key: (1, 1)}, cache_size=2)
        second = AffinityScorer(profile, taste, taste_features={key: (-1, 1)}, cache_size=2)
        products = [self.product(title='Хлопковая рубашка') for _ in range(10)]
        for product in products:
            self.assertGreater(first.score(product), second.score(product))
        self.assertEqual(first.cached_products, 2)
        self.assertEqual(second.cached_products, 2)
        old = first.evaluate(products[-1])
        self.assertIs(first.evaluate(products[-1]), old)
        products[-1].title = 'Льняная рубашка'
        fresh = AffinityScorer(profile, taste, taste_features={key: (1, 1)})
        self.assertLess(fresh.score(products[-1]), old.affinity)

    def test_duplicate_save_does_not_mutate_state_in_outer_transaction(self):
        product = self.product()
        self.db.commit()
        payload = EventRequest(product_id=product.id, event_type='save')
        record_product_event(self.db, self.user, payload, commit=False)
        state = self.db.scalar(select(UserProductState))
        original = (state.event_strength, state.updated_at, state.hidden_until)
        profile = self.db.scalar(select(TasteProfile))
        version = profile.profile_version
        repeat = record_product_event(self.db, self.user, payload, commit=False)
        self.assertTrue(repeat['idempotent'])
        self.assertEqual((state.event_strength, state.updated_at, state.hidden_until), original)
        self.db.commit()
        self.db.refresh(state)
        self.assertEqual(state.event_strength, original[0])
        self.assertEqual(profile.profile_version, version)
        self.assertEqual(len(list(self.db.scalars(select(RecommendationEventV2)))), 1)

    def test_feedback_service_participates_in_callers_rollback(self):
        product = self.product()
        self.db.commit()
        record_product_event(self.db, self.user,
            EventRequest(product_id=product.id, event_type='like'), commit=False)
        self.assertEqual(len(list(self.db.scalars(select(RecommendationEventV2)))), 1)
        self.assertTrue(list(self.db.scalars(select(UserTasteFeature))))
        self.db.rollback()
        for model in (RecommendationEventV2, UserProductState, UserTasteFeature, TasteProfile):
            self.assertEqual(list(self.db.scalars(select(model))), [])
