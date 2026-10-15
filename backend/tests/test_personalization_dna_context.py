from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from uuid import uuid4
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import FitProfile, Product, TasteProfile, User, UserTasteFeature
from app.services import activation_service as activation
from app.services.catalog.fashion_taxonomy import TAXONOMY
from app.services.catalog.product_dna import reliable_taste_features
from app.services.catalog_filters import CatalogFilters
from app.services.mie_scoring import load_user_taste_feature_map
from app.services.recommendation_context import ShoppingContext
from app.services.user_twin_service import DNA_TASTE_PREFIX, apply_event_to_user_twin


class PersonalizationDNAContextTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, autoflush=False)()
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        self.user = User(id=str(uuid4()), email='context@example.test', password_hash='unused')
        self.db.add(self.user); self.db.flush()
        self.session = activation.configure(self.db, self.user, gender_target='womenswear', age_group='25-34', height=170,
            clothing_size='M', shoe_size='38', clothing_size_system='LETTER', shoe_size_system='EU',
            styles=['casual', 'minimalism'], price_segment='mass')
        self.session.completed_at = datetime.now(timezone.utc)
        self.fit = self.db.scalar(select(FitProfile).where(FitProfile.user_id == self.user.id))
        self.fit.interest_categories = ['рубашки']
        self.db.flush()

    def product(self, **changes):
        pid = str(uuid4())
        values = dict(id=pid, external_id=pid, source='baon', title='Рубашка', brand='BAON',
            category='рубашки', price=2000, currency='RUB', gender_target='womenswear',
            available_sizes=['M'], feed_raw_json={'size_system': 'LETTER'}, colors=['black'],
            style_tags=['casual'], image_url=f'https://baon.ru/{pid}.jpg',
            product_url=f'https://baon.ru/product/{pid}', is_active=1, is_available=1, availability_status='in_stock',
            is_deleted_from_feed=0, last_seen_in_feed_at=datetime.now(timezone.utc))
        values.update(changes)
        row = Product(**values); self.db.add(row); self.db.flush()
        return row

    def test_taxonomy_definitions_and_explicit_attributes(self):
        self.assertEqual(sum(map(len, TAXONOMY.values())), 79)
        value = self.product(feed_raw_json={'print': 'в полоску', 'length': 'миди',
            'sleeve': 'long', 'neckline': 'v_neck', 'rise': 'high', 'texture': 'плиссе',
            'occasion': 'для офиса', 'formality': 'smart casual'})
        features = reliable_taste_features(value)
        self.assertEqual(features, {'pattern': {'striped'}, 'length': {'midi'}, 'sleeve': {'long'},
            'neckline': {'v_neck'}, 'rise': {'high'}, 'texture': {'pleated'}, 'occasion': {'work'}, 'formality': {'smart'}})
        value.feed_raw_json = {'length': ['мини', 'макси'], 'sleeve': ['long', 'short']}
        self.assertNotIn('length', reliable_taste_features(value))
        self.assertNotIn('sleeve', reliable_taste_features(value))

    def test_active_feed_uses_learned_material_before_candidate_cutoff(self):
        training = self.product(title='Хлопковая рубашка')
        for _ in range(10):
            apply_event_to_user_twin(self.db, user_id=self.user.id, product=training, event_type='like')
        for i in range(60):
            self.product(id=f'a{i:035}', title='Льняная рубашка')
        wanted = self.product(id='z'*36, title='Хлопковая рубашка')
        rows = activation.personalized_feed(self.db, self.user, self.session, exclude_ids=[training.id],
            filters=CatalogFilters(categories=('tops',)))
        self.assertEqual(len(rows), 20)
        self.assertEqual(rows[0].product.id, wanted.id)
        self.assertGreater(rows[0].breakdown['taste_score'], rows[1].breakdown['taste_score'])
        wrong_size = self.product(title='Хлопковая рубашка', available_sizes=['L'])
        self.assertNotIn(wrong_size.id, {r.product.id for r in activation.personalized_feed(
            self.db, self.user, self.session, filters=CatalogFilters(categories=('tops',)))})

    def test_context_changes_order_without_changing_permanent_taste(self):
        work = self.product(id='z'*36, feed_raw_json={'occasion': 'work', 'style': 'business'})
        evening = self.product(id='a'*36, feed_raw_json={'occasion': 'evening', 'style': 'elegant'})
        taste = self.db.scalar(select(TasteProfile).where(TasteProfile.user_id == self.user.id))
        before = deepcopy((taste.style_weights, self.fit.style_scenarios))
        office = activation.personalized_feed(self.db, self.user, self.session, scenario='office')
        night = activation.personalized_feed(self.db, self.user, self.session, scenario='evening')
        self.assertEqual(office[0].product.id, work.id)
        self.assertEqual(night[0].product.id, evening.id)
        self.assertEqual(before, (taste.style_weights, self.fit.style_scenarios))
        self.assertEqual(list(self.db.scalars(select(UserTasteFeature))), [])

    def test_context_uses_explicit_season_and_budget_only(self):
        winter = self.product(feed_raw_json={'season': 'winter'})
        unknown = self.product(description='Для зимы и вечера')
        summer = ShoppingContext(season='summer', budget_max=2000)
        self.assertFalse(summer.allows(winter))
        self.assertTrue(summer.allows(unknown))
        self.assertFalse(ShoppingContext(budget_max=1000).allows(unknown))
        self.assertTrue(ShoppingContext().allows(winter))

    def test_context_dislike_does_not_train_style_or_occasion(self):
        product = self.product(title='Хлопковая рубашка', feed_raw_json={'occasion': 'work'})
        rows = apply_event_to_user_twin(self.db, user_id=self.user.id, product=product,
            event_type='dislike', meta={'reason': 'not_for_context', 'scenario': 'evening'})
        self.assertEqual({r.feature_type for r in rows}, {'item'})
        positive = apply_event_to_user_twin(self.db, user_id=self.user.id, product=product, event_type='like')
        self.assertNotIn('occasion', {r.feature_type for r in positive})

    def test_context_uses_explicit_formality_but_unknown_stays_neutral(self):
        formal = self.product(feed_raw_json={'formality': 'formal'})
        casual = self.product(feed_raw_json={'formality': 'casual'})
        unknown = self.product(description='Уместна в любом офисе и на вечеринке')
        office = ShoppingContext('office')
        self.assertGreater(office.score(formal)[0], office.score(casual)[0])
        self.assertEqual(office.score(unknown), (.5, []))
        self.assertGreater(ShoppingContext('daily').score(casual)[0], ShoppingContext('daily').score(formal)[0])

    def test_selective_dna_matches_full_evidence_including_conflicts(self):
        product = self.product(title='Хлопковая рубашка',
            feed_raw_json={'material': 'linen', 'pattern': 'striped', 'season': 'winter'},
            raw_params_json={'Рисунок': 'floral', 'Цвет': 'Черный'})
        full = reliable_taste_features(product)
        for dimension in TAXONOMY:
            self.assertEqual(reliable_taste_features(product, dimensions=(dimension,)),
                {dimension: full[dimension]} if dimension in full else {})

    def test_confidence_decays_on_reads_without_changing_evidence(self):
        product = self.product(title='Хлопковая рубашка')
        now = datetime.now(timezone.utc)
        for _ in range(5):
            apply_event_to_user_twin(self.db, user_id=self.user.id, product=product,
                event_type='like', occurred_at=now-timedelta(days=180))
        row = self.db.scalar(select(UserTasteFeature).where(UserTasteFeature.feature_type == 'material'))
        before = (row.preference_score, row.confidence, row.last_decay_at, row.updated_at)
        scores = load_user_taste_feature_map(self.db, user_id=self.user.id, as_of=now)
        score, confidence = scores[('material', DNA_TASTE_PREFIX+'cotton')]
        self.assertLess(score, row.preference_score)
        self.assertLess(confidence, row.confidence)
        self.assertEqual(before, (row.preference_score, row.confidence, row.last_decay_at, row.updated_at))

    def test_search_reranks_matches_without_changing_intent_or_leaking_size(self):
        from app.api.products import products_list
        cotton = self.product(title='Хлопковая рубашка')
        linen = self.product(title='Льняная рубашка')
        wrong = self.product(title='Хлопковая рубашка', available_sizes=['L'])
        for _ in range(8):
            apply_event_to_user_twin(self.db, user_id=self.user.id, product=cotton, event_type='like')
        # HTTP reads release authentication before opening an independent snapshot.
        self.db.expire_on_commit = False
        self.db.commit()
        with patch('app.api.products.user_from_token', return_value=self.user):
            rows = products_list(q='рубашка', db=self.db, credentials=object())
            self.assertEqual(rows[0]['id'], cotton.id)
            self.assertNotIn(wrong.id, {r['id'] for r in rows})
            self.assertEqual(products_list(q='неттакоготовара', db=self.db, credentials=object()), [])
            # A search alone does not become a learned preference.
            before = load_user_taste_feature_map(self.db, user_id=self.user.id, as_of=datetime(2026, 1, 1, tzinfo=timezone.utc))
            products_list(q='рубашка', db=self.db, credentials=object())
            self.assertEqual(before, load_user_taste_feature_map(self.db, user_id=self.user.id, as_of=datetime(2026, 1, 1, tzinfo=timezone.utc)))
        other = User(id=str(uuid4()), email='other@example.test', password_hash='unused')
        self.db.add(other); self.db.flush()
        session = activation.configure(self.db, other, gender_target='womenswear', age_group='25-34', height=170, clothing_size='M',
            shoe_size='38', clothing_size_system='LETTER', shoe_size_system='EU',
            styles=['casual', 'minimalism'], price_segment='mass')
        session.completed_at = datetime.now(timezone.utc)
        for _ in range(8):
            apply_event_to_user_twin(self.db, user_id=other.id, product=linen, event_type='like')
        self.db.commit()
        with patch('app.api.products.user_from_token', return_value=other):
            self.assertEqual(products_list(q='рубашка', db=self.db, credentials=object())[0]['id'], linen.id)

    def test_search_pagination_is_stable_for_equal_scores(self):
        from app.api.products import products_list
        expected = {self.product().id for _ in range(45)}
        self.db.expire_on_commit = False
        self.db.commit()
        with patch('app.api.products.user_from_token', return_value=self.user):
            pages = [products_list(q='рубашка', limit=20, offset=i, db=self.db, credentials=object())
                     for i in (0, 20, 40)]
        ids = [row['id'] for page in pages for row in page]
        self.assertEqual(len(ids), 45)
        self.assertEqual(set(ids), expected)

    def test_outfit_evidence_affects_compatibility_and_season_guard(self):
        from app.services.outfit_quality import pair_compatible
        from app.services.outfit_engine_v2 import SlotCandidate, compatibility_score
        top = self.product(feed_raw_json={'pattern': 'striped', 'season': 'summer'})
        bottom = self.product(title='Брюки', category='брюки', feed_raw_json={'pattern': 'floral', 'season': 'winter'})
        self.assertFalse(pair_compatible(top, bottom))
        def score():
            items = [SlotCandidate('top', top, .8, 1, .8, .7), SlotCandidate('bottom', bottom, .8, 1, .8, .7)]
            return compatibility_score(items, scenario='daily')
        conflicting = score()[0]
        bottom.feed_raw_json = {'pattern': 'solid', 'season': 'summer'}
        self.assertTrue(pair_compatible(top, bottom))
        self.assertGreater(score()[0], conflicting)


if __name__ == '__main__': unittest.main()
