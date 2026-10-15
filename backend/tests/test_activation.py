import unittest
from datetime import datetime, timezone
from uuid import uuid4
from fastapi import HTTPException
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker
from app.db import Base
from app.models import User, Product, FitProfile, TasteProfile, StyleProfile, ActivationExposure, RecommendationEventV2
from app.services import activation_service as a
from app.services.catalog_filters import CatalogFilters
from app.services.product_complements import complementary_products
from app.services.garment_roles import product_role
from app.services.product_identity import product_identity_keys
from app.api.products import _feed_scored_items
from app.api.recommendations import record_product_event, EventRequest


class ActivationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, autoflush=False)()
        self.user = User(id=str(uuid4()), email='activation@example.test', password_hash='unused')
        self.db.add(self.user); self.db.flush()
        self.session = a.configure(self.db, self.user, gender_target='womenswear', age_group='25-34', height=170, clothing_size='M', shoe_size='38',
            styles=['casual','minimalism'], price_segment='mass', clothing_size_system='LETTER', shoe_size_system='EU')
        self.fit = self.db.scalar(select(FitProfile).where(FitProfile.user_id == self.user.id))

    def tearDown(self):
        self.db.close(); self.engine.dispose()

    def product(self, category='рубашки', **overrides):
        pid = str(uuid4())
        title = {'рубашки':'Рубашка однотонная', 'брюки':'Брюки прямые', 'обувь':'Лоферы кожаные',
                 'платья':'Платье вечернее', 'юбки':'Юбка миди', 'футболки':'Футболка базовая'}[category]
        fields = dict(id=pid, external_id=pid, source='befree', brand=f'Brand {pid[:5]}',
            title=title, category=category, price=1500, gender_target='womenswear',
            image_url=f'https://befree.ru/images/{pid}.jpg', product_url=f'https://befree.ru/product/{pid}',
            available_sizes=['38'] if category=='обувь' else ['M'], colors=['black'],
            style_tags=['casual','minimalism','elegant'], is_active=1, is_available=1, availability_status='in_stock', is_deleted_from_feed=0,
            feed_raw_json={'size_system':'EU' if category=='обувь' else 'LETTER'},
            last_seen_in_feed_at=datetime.now(timezone.utc))
        fields.update(overrides)
        p = Product(**fields); self.db.add(p); self.db.flush(); return p

    def seed(self, n=8):
        return [self.product(cat) for cat in ['рубашки','брюки','обувь','платья','юбки','футболки'] for _ in range(n)]

    def learn(self):
        deck = a.swipe_deck(self.db, self.user, self.session)
        for row in deck['products'][:15]:
            a.swipe_feedback(self.db, self.user, self.session, row['id'], 'like')
        return deck

    def test_cold_feed_and_completion_cannot_be_bypassed(self):
        with self.assertRaises(HTTPException) as err: _feed_scored_items(self.db,self.user,limit=20)
        self.assertEqual(err.exception.status_code,409)
        with self.assertRaises(HTTPException): a.complete_wow(self.db,self.user,self.session)
        self.assertIsNone(self.session.completed_at)

    def test_twenty_real_distinct_cards_and_durable_idempotent_taste(self):
        self.seed(); deck=a.swipe_deck(self.db,self.user,self.session)
        self.assertEqual(len(deck['products']),20)
        pid=deck['products'][0]['id']
        a.swipe_feedback(self.db,self.user,self.session,pid,'like'); self.db.commit()
        taste=self.db.scalar(select(TasteProfile).where(TasteProfile.user_id==self.user.id))
        weights=dict(taste.style_weights)
        a.swipe_feedback(self.db,self.user,self.session,pid,'dislike'); self.db.commit(); self.db.expire_all()
        self.assertEqual(taste.style_weights,weights)
        self.assertTrue(taste.liked_styles and taste.liked_categories and taste.liked_colors and taste.liked_brands)
        self.assertEqual(a.swipe_deck(self.db,self.user,self.session)['rated'],1)
        self.assertNotIn(pid,[p['id'] for p in a.swipe_deck(self.db,self.user,self.session)['products']])
        self.assertEqual(self.db.scalar(select(func.count()).select_from(RecommendationEventV2)),1)

    def test_unavailable_photo_does_not_train_taste_or_satisfy_gate(self):
        self.seed(); deck=a.swipe_deck(self.db,self.user,self.session)
        for p in deck['products']:
            a.swipe_feedback(self.db,self.user,self.session,p['id'],'unavailable')
        self.assertEqual(a.rated_count(self.session),0)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(RecommendationEventV2)),0)
        with self.assertRaises(HTTPException): a.complete_wow(self.db,self.user,self.session)
        self.assertTrue(a.swipe_deck(self.db,self.user,self.session)['products'])

    def test_required_preferences_and_unissued_reaction_rejected(self):
        with self.assertRaises(HTTPException):
            a.configure(self.db,self.user,gender_target='womenswear', age_group='25-34', height=170,clothing_size='M',shoe_size='38', shoe_size_system='EU',styles=['casual'],price_segment='mass')
        with self.assertRaises(HTTPException): a.swipe_feedback(self.db,self.user,self.session,str(uuid4()),'like')

    def test_only_dislikes_cannot_enter_empty_feed_and_get_more_choices(self):
        self.seed(12)
        deck=a.swipe_deck(self.db,self.user,self.session)
        for p in deck['products']:
            a.swipe_feedback(self.db,self.user,self.session,p['id'],'dislike')
        with self.assertRaises(HTTPException): a.complete_wow(self.db,self.user,self.session)
        more=a.swipe_deck(self.db,self.user,self.session)
        self.assertTrue(more['products']); self.assertFalse(more['can_continue'])
        self.assertEqual(more['target'],30)
        result=a.swipe_feedback(self.db,self.user,self.session,more['products'][0]['id'],'like')
        self.assertTrue(result['can_continue'])

    def test_merchant_color_trains_taste_when_normalized_colors_missing(self):
        p=self.product(colors=[],color_original='Синий')
        self.session.swipe_product_ids=[p.id]; self.db.flush()
        a.swipe_feedback(self.db,self.user,self.session,p.id,'like')
        taste=self.db.scalar(select(TasteProfile).where(TasteProfile.user_id==self.user.id))
        self.assertIn('blue',taste.liked_colors)
        self.assertGreater(taste.color_weights['blue'],0)
        self.assertEqual(a.features(p)['color'],{'blue'})

    def test_strict_quality_does_not_relax_on_empty_results(self):
        good=self.product()
        for overrides in [dict(gender_target='menswear'),dict(gender_target='unisex'),dict(source='vipavenue'),
            dict(source='demix'),dict(title='Рубашка для девочек'),dict(title='Крем для лица'),
            dict(title='Топ BAON',description='Спортивный топ для фитнеса.'),
            dict(title='Топ BAON',raw_params_json={'param_назначение':'Для фитнеса'}),
            dict(price=10001),dict(price=1),dict(available_sizes=[]),dict(available_sizes=['L']),
            dict(image_url=''),dict(product_url=''),dict(is_available=0),dict(currency='USD')]:
            self.product(**overrides)
        self.assertEqual([p.id for p in a.candidate_pool(self.db,self.user,self.session)],[good.id])
        self.assertEqual(a.candidate_pool(self.db,self.user,self.session,filters=CatalogFilters(max_price=1000)),[])
        self.assertFalse(a.confirmed_size(self.product('обувь',available_sizes=['M']),self.fit,self.session))

    def test_liked_candidates_enter_pool_before_category_cutoff(self):
        for i in range(55): self.product(id=f'a{i:035}',brand='Other')
        wanted=self.product(id='z'*36,brand='Favourite')
        _,_,taste=a._preferences(self.db,self.user); taste.brand_weights={'favourite':30}; self.db.flush()
        rows=a.candidate_pool(self.db,self.user,self.session)
        self.assertEqual(rows[0].id,wanted.id)

    def test_merchant_category_paths_and_single_brand_do_not_empty_activation(self):
        self.seed()
        for p in self.db.scalars(select(Product)):
            p.category='Женская одежда / '+p.category
            p.brand='Only confirmed-size brand'
        self.db.flush()
        deck=a.swipe_deck(self.db,self.user,self.session)
        self.assertEqual(len(deck['products']),20)

    def test_shared_accessory_folder_keeps_footwear_but_rejects_accessories(self):
        from app.models import SourceRule
        from app.services.catalog.rule_filters import product_passes_source_rules
        source_id=str(uuid4())
        rules={source_id:[SourceRule(id=str(uuid4()),source_id=source_id,rule_type='blocked_category',rule_value='аксессуар',is_active=1)]}
        shoe=self.product('обувь',source_id=source_id)
        shoe.category='женщинам/обувь_и_аксессуары/обувь/балетки'
        self.assertTrue(product_passes_source_rules(shoe,rules))
        from app.services.outfit_quality import scenario_product_ok
        shoe.merchant_category='Обувь и аксессуары'
        self.assertTrue(scenario_product_ok(shoe,'daily'))
        self.assertTrue(scenario_product_ok(shoe,'evening'))
        shoe.category='женщинам/обувь_и_аксессуары/аксессуары/ремни'
        self.assertFalse(product_passes_source_rules(shoe,rules))

    def test_dominant_affordable_brand_cannot_strand_swipe_onboarding(self):
        from collections import Counter
        from app.catalog_normalize import normalize_category
        # Real XS/economy shape: most garments share a brand; the other brands
        # supply footwear only. Neither brand nor category counts alone suffice.
        garments=[self.product(cat,brand='BAON') for cat in
                  ['футболки','платья','юбки','рубашки','брюки'] for _ in range(8)]
        shoes=[self.product('обувь',brand=f'Shoe brand {i}') for i in range(12)]
        rows=a.diverse_products(garments+shoes,20)
        self.assertEqual(len(rows),20)
        self.assertEqual(len({p.id for p in rows}),20)
        categories=[normalize_category(p.category) for p in rows]
        self.assertLessEqual(max(Counter(categories).values()),5)
        self.assertTrue(all(len(set(categories[i:i+3]))>1 for i in range(18)))
        deck=a.swipe_deck(self.db,self.user,self.session)
        self.assertEqual(len(deck['products']),20)

    def test_brand_fallback_still_prefers_diversity_and_never_pads_one_category(self):
        from collections import Counter
        products=[self.product(cat,brand=brand) for brand in ('A','B','C')
                  for cat in ('футболки','платья','рубашки','брюки','юбки','обувь') for _ in range(8)]
        rows=a.diverse_products(products,20)
        self.assertEqual(len(rows),20)
        self.assertLessEqual(max(Counter(p.brand for p in rows).values()),7)
        only_shoes=[self.product('обувь',brand='A') for _ in range(20)]
        self.assertEqual(len(a.diverse_products(only_shoes,20)),2)

    def test_wow_is_complete_within_total_budget_unique_and_retry_safe(self):
        self.seed(10); self.learn()
        result=a.complete_wow(self.db,self.user,self.session); self.db.commit()
        self.assertGreaterEqual(len(result['outfits']),2)
        seen=set()
        for outfit in result['outfits']:
            self.assertLessEqual(outfit['total_price'],10000)
            products=[self.db.get(Product,pid) for pid in outfit['items'].values()] if 'items' in outfit else [self.db.get(Product,pid) for pid in outfit['items_json'].values()]
            roles={product_role(p) for p in products}
            self.assertTrue({'one_piece','shoes'}<=roles or {'top','bottom','shoes'}<=roles)
            for p in products:
                self.assertTrue(a.product_ok(p,self.fit,self.session))
                keys=product_identity_keys(p); self.assertFalse(keys & seen); seen.update(keys)
        again=a.complete_wow(self.db,self.user,self.session)
        self.assertEqual([o['id'] for o in result['outfits']],[o['id'] for o in again['outfits']])

    def test_sparse_wow_does_not_make_incomplete_or_expensive_looks(self):
        self.seed(); self.learn()
        for p in self.db.scalars(select(Product).where(Product.category=='обувь')): p.available_sizes=[]
        self.db.flush()
        result=a.complete_wow(self.db,self.user,self.session)
        self.assertTrue(result['completed']); self.assertEqual(result['outfits'],[])

    def test_prefetch_not_impression_metrics_union_and_limit(self):
        self.seed(12); self.learn(); self.session.completed_at=datetime.now(timezone.utc); self.db.flush()
        rows=a.personalized_feed(self.db,self.user,self.session,limit=20)
        self.assertEqual(len(rows),20)
        # The separate 80/20 slate policy survives five-factor ranking.
        self.assertEqual(sum('soft_exploration' in row.candidate_sources for row in rows),4)
        self.assertTrue(all('exploration_score' in row.breakdown for row in rows))
        self.assertTrue(all(0 <= row.breakdown['exploration_score'] <= 1 for row in rows))
        self.assertEqual(a.activation_metrics(self.db)['unique_first20_views'],0)
        a.record_exposure_event(self.db,self.user,rows[0].product.id,'open_product')
        self.assertEqual(a.activation_metrics(self.db)['unique_first20_views'],0)
        for row in rows:
            a.record_exposure_event(self.db,self.user,row.product.id,'view',surface='feed')
            a.record_exposure_event(self.db,self.user,row.product.id,'view',surface='feed')
        first=rows[0].product.id
        for event in ['like','save','open_product','affiliate_click']: a.record_exposure_event(self.db,self.user,first,event)
        next_rows=a.personalized_feed(self.db,self.user,self.session,exclude_ids=[r.product.id for r in rows])
        for row in next_rows:a.record_exposure_event(self.db,self.user,row.product.id,'view',surface='feed')
        metrics=a.activation_metrics(self.db)
        self.assertEqual(metrics['unique_first20_views'],20)
        self.assertEqual(metrics['like_or_save'],1)
        self.assertEqual(metrics['affiliate_click_rate'],5)
        self.assertEqual(metrics['reached_card10_rate'],100)

    def test_feed_interest_categories_and_complements_missing_roles(self):
        self.seed(); self.learn(); self.session.completed_at=datetime.now(timezone.utc)
        _,_,taste=a._preferences(self.db,self.user)
        taste.liked_categories=['рубашки']; taste.category_weights={'рубашки':10}; self.db.flush()
        rows=a.personalized_feed(self.db,self.user,self.session)
        self.assertTrue(rows)
        self.assertEqual({r.product.category for r in rows},{'рубашки'})
        anchor=rows[0].product
        complements=complementary_products(self.db,self.user,anchor)
        self.assertTrue(complements)
        self.assertTrue({product_role(p) for p in complements} <= {'bottom','shoes'})
        self.assertTrue(all(a.product_ok(p,self.fit,self.session) for p in complements))

    def test_activation_has_no_legacy_milestone_interruptions(self):
        self.seed(); self.learn(); self.session.completed_at=datetime.now(timezone.utc); self.db.flush()
        p=self.product()
        for _ in range(15):
            result=record_product_event(self.db,self.user,EventRequest(product_id=p.id,event_type='view'))
            self.assertFalse(result['milestone_reached'])

    def test_warm_existing_male_profile_keeps_access_without_reconfiguration(self):
        from app.models import ActivationSession
        self.seed(); self.learn()
        self.fit.gender_target='menswear'
        self.db.scalar(select(StyleProfile).where(StyleProfile.user_id==self.user.id)).completed_at=datetime.now(timezone.utc)
        self.db.delete(self.session); self.db.flush()
        self.assertIsNone(a.require_warm_feed(self.db,self.user))
        self.assertEqual(self.fit.gender_target,'menswear')
