from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from unittest.mock import patch
import unittest
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session
from app.db import Base
from app.models import User, Product, TasteProfile, FitProfile, ShoppingIntent, UserTasteFeature
from app.services import activation_service as activation
from app.services import shopping_intent_service as intents
from app.services.shopping_intent_parser import parse_intent
from app.services.intent_eligibility import intent_product_problem
from app.schemas.shopping_intent import IntentSpec


class IntentParserTests(unittest.TestCase):
    def test_office_trousers_and_budget(self):
        spec = parse_intent('Мне нужны чёрные брюки в офис до 7 000 рублей')
        self.assertEqual(spec.unresolved, [])
        self.assertEqual(spec.requested_categories, ['брюки'])
        self.assertEqual(spec.colors, ['black'])
        self.assertEqual((spec.occasion, spec.budget_max), ('office', 7000))

    def test_outfit_exclusions_and_thousands(self):
        spec = parse_intent('Собери мне образ на свадьбу до 20 тысяч, только без платья и каблуков')
        self.assertEqual(spec.unresolved, [])
        self.assertEqual((spec.intent_type, spec.occasion, spec.budget_max), ('outfit', 'wedding', 20000))
        self.assertEqual(spec.excluded_categories, ['платья'])
        self.assertEqual(spec.excluded_features, {'heel': ['heels']})

    def test_color_wish_is_soft_budget_is_hard(self):
        spec = parse_intent('Хочу платье до 8000, желательно чёрное')
        self.assertEqual(spec.unresolved, [])
        self.assertEqual(spec.colors, [])
        self.assertEqual(spec.preferred_colors, ['black'])
        self.assertEqual(spec.budget_max, 8000)

    def test_refinements_keep_task_constraints(self):
        initial = parse_intent('Чёрные брюки в офис до 7000')
        cheaper = parse_intent('Слишком дорого', previous=initial)
        self.assertEqual(cheaper.budget_max, 5600)
        changed = parse_intent('Другого цвета', previous=cheaper)
        self.assertEqual(changed.colors, [])
        self.assertEqual(changed.excluded_colors, ['black'])
        tight = parse_intent('Только не оверсайз', previous=changed)
        self.assertEqual(tight.excluded_features, {'fit': ['oversized']})
        self.assertEqual((tight.occasion, tight.budget_max, tight.requested_categories), ('office', 5600, ['брюки']))
        self.assertEqual(initial.budget_max, 7000)

    def test_unsupported_and_contradictory_requests_are_not_silently_relaxed(self):
        for text in ('Брюки из кожи дракона', 'Платье от 9000 до 8000', 'Платье до 2000000',
                     'Хочу что-то похожее на это пальто, но дешевле', 'Платье чёрное, только не чёрное'):
            with self.subTest(text=text):
                self.assertTrue(parse_intent(text).unresolved)


class ShoppingIntentTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.addCleanup(self.db.close)
        self.user = User(id=str(uuid4()), email='intent@example.test', password_hash='unused')
        self.db.add(self.user); self.db.flush()
        self.session = activation.configure(self.db, self.user, age_group='25-34', height=168,
            clothing_size='48', clothing_size_system='RU', shoe_size='38', shoe_size_system='EU',
            styles=['casual', 'minimalism'], price_segment='mid')
        self.session.completed_at = datetime.now(timezone.utc)
        self.fit = self.db.scalar(select(FitProfile).where(FitProfile.user_id == self.user.id))
        self.db.commit()

    def product(self, **changes):
        pid = str(uuid4())
        values = dict(id=pid, external_id=pid, source='baon', title='Брюки', category='брюки', brand='BAON',
            gender_target='womenswear', price=6000, currency='RUB', available_sizes=['48'],
            feed_raw_json={'size_system': 'RU', 'color': 'black', 'fit': 'regular', 'silhouette': 'straight'},
            is_active=1, is_available=1, availability_status='in_stock', is_deleted_from_feed=0,
            image_url=f'https://baon.ru/{pid}.jpg', product_url=f'https://baon.ru/product/{pid}',
            last_seen_in_feed_at=datetime.now(timezone.utc))
        values.update(changes)
        product = Product(**values); self.db.add(product); self.db.flush()
        return product

    def create(self, text='Чёрные брюки в офис до 7000', request_id=None):
        return intents.create_intent(self.db, self.user, text=text, request_id=request_id or uuid4().hex)

    def test_exact_size_price_color_and_stock_precede_score(self):
        good = self.product()
        self.product(price=7001)
        self.product(available_sizes=['42', '44', '46'])
        self.product(availability_status='unknown')
        self.product(last_seen_in_feed_at=datetime.now(timezone.utc)-timedelta(hours=73))
        self.product(feed_raw_json={'size_system': 'RU', 'color': 'white'})
        self.product(feed_raw_json={'size_system': 'RU'})  # Unknown colour fails a hard colour request.
        scored = []
        with patch.object(intents.AffinityScorer, 'score', side_effect=lambda p: scored.append(p.id) or .5):
            response = intents.intent_response(self.db, self.user, self.create(), remember=True)
        self.assertEqual(scored, [good.id])
        self.assertEqual([p['product']['id'] for p in response['products']], [good.id])

    def test_soft_color_and_unknown_exclusions(self):
        white = self.product(title='Платье', category='платья',
            feed_raw_json={'size_system': 'RU', 'color': 'white'})
        response = intents.intent_response(self.db, self.user, self.create('Платье до 8000, желательно чёрное'))
        self.assertEqual(response['products'][0]['product']['id'], white.id)
        unknown = self.product(feed_raw_json={'size_system': 'RU'})
        self.assertEqual(intent_product_problem(unknown, self.fit, parse_intent('Брюки не широкие')), 'excluded_feature_or_unknown')

    def test_session_changes_do_not_change_twin_and_references_are_owned(self):
        product = self.product(price=6000)
        taste = self.db.scalar(select(TasteProfile).where(TasteProfile.user_id == self.user.id))
        before = deepcopy((taste.style_weights, taste.color_weights, taste.profile_version, self.fit.size_profile_json))
        intent = self.create()
        intents.intent_response(self.db, self.user, intent, remember=True)
        updated = intents.refine_intent(self.db, self.user, intent.id, text='Слишком дорого',
            expected_version=1, reference_product_id=product.id)
        self.assertEqual(updated.constraints_json['budget_max'], 4800)
        self.assertEqual(updated.version, 2)
        self.assertEqual(before, (taste.style_weights, taste.color_weights, taste.profile_version, self.fit.size_profile_json))
        self.assertEqual(self.db.scalar(select(func.count()).select_from(UserTasteFeature)), 0)
        with self.assertRaises(intents.IntentError) as error:
            intents.refine_intent(self.db, self.user, intent.id, text='Дешевле', expected_version=1)
        self.assertEqual(error.exception.status, 409)
        with self.assertRaises(intents.IntentError):
            intents.refine_intent(self.db, self.user, intent.id, text='Дешевле', expected_version=2, reference_product_id=' чужой товар')

    def test_idempotent_creation_ownership_expiry_and_read_only_taste(self):
        intent = self.create(request_id='fixed-idempotency-key')
        self.assertEqual(self.create(request_id='fixed-idempotency-key').id, intent.id)
        with self.assertRaises(intents.IntentError) as error:
            self.create(text='Хочу платье', request_id='fixed-idempotency-key')
        self.assertEqual(error.exception.status, 409)
        other = User(id=str(uuid4()), email='other@example.test', password_hash='unused')
        self.db.add(other); self.db.flush()
        with self.assertRaises(intents.IntentError) as error:
            intents.get_intent(self.db, other, intent.id)
        self.assertEqual(error.exception.status, 404)
        with self.assertRaises(intents.IntentError) as error:
            intents.get_intent(self.db, self.user, intent.id, now=intent.expires_at)
        self.assertEqual(error.exception.status, 410)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(ShoppingIntent)), 1)

    def test_unknown_requirement_blocks_every_result(self):
        self.product()
        result = intents.intent_response(self.db, self.user, self.create('Брюки без аллергенов'))
        self.assertEqual(result['state'], 'needs_clarification')
        self.assertEqual(result['products'], [])

    def test_no_heel_requires_explicit_flat_evidence(self):
        spec = parse_intent('Обувь без каблуков')
        shoes = self.product(category='обувь', title='Туфли', available_sizes=['38'],
            feed_raw_json={'size_system': 'EU'})
        self.assertEqual(intent_product_problem(shoes, self.fit, spec), 'excluded_feature_or_unknown')
        shoes.feed_raw_json = {'size_system': 'EU', 'heel_height_cm': '0'}
        self.assertIsNone(intent_product_problem(shoes, self.fit, spec))
        shoes.feed_raw_json = {'size_system': 'EU', 'heel_height_cm': '5'}
        self.assertEqual(intent_product_problem(shoes, self.fit, spec), 'excluded_feature_or_unknown')

    def seed_outfit(self):
        self.product(title='Рубашка', category='рубашки', price=5000)
        self.product(title='Брюки', price=6000)
        flat = self.product(category='обувь', title='Туфли на плоской подошве', price=4000,
            available_sizes=['38'], feed_raw_json={'size_system': 'EU', 'heel_height_cm': '0'})
        alternative = self.product(category='обувь', title='Лоферы', price=4500,
            available_sizes=['38'], feed_raw_json={'size_system': 'EU', 'heel_height_cm': '0'})
        return flat, alternative

    def test_wedding_outfit_exact_total_exclusions_and_replacement_locks(self):
        self.seed_outfit()
        high = self.product(category='обувь', title='Туфли на каблуке', price=3000,
            available_sizes=['38'], feed_raw_json={'size_system': 'EU', 'heel_height_cm': '5'})
        self.product(category='платья', title='Платье', price=1000)
        intent = self.create('Образ на свадьбу до 20000 без платья и каблуков')
        result = intents.intent_response(self.db, self.user, intent, remember=True)
        self.assertTrue(result['outfits'])
        before = result['outfits'][0]
        self.assertEqual(set(before['items']), {'top', 'bottom', 'shoes'})
        self.assertNotEqual(before['items']['shoes'], high.id)
        self.assertLessEqual(before['total_price'], 20000)
        updated = intents.refine_intent(self.db, self.user, intent.id, text='Оставь верх, поменяй обувь',
            expected_version=1, reference_outfit_id=before['id'])
        after = intents.intent_response(self.db, self.user, updated, remember=True)['outfits'][0]
        self.assertEqual(after['items']['top'], before['items']['top'])
        self.assertEqual(after['items']['bottom'], before['items']['bottom'])
        self.assertNotEqual(after['items']['shoes'], before['items']['shoes'])
        self.assertLessEqual(after['total_price'], 20000)

    def test_outfit_has_no_partial_fallback_and_revalidates_when_saved(self):
        self.seed_outfit()
        too_cheap = self.create('Образ до 10000 без платья')
        self.assertEqual(intents.intent_response(self.db, self.user, too_cheap)['outfits'], [])
        intent = self.create('Образ в офис до 20000 без платья')
        result = intents.intent_response(self.db, self.user, intent, remember=True)
        outfit = result['outfits'][0]
        saved = intents.save_intent_outfit(self.db, self.user, intent.id, outfit['id'], expected_version=1)
        self.assertEqual(saved['status'], 'ok')
        from app.models import Outfit
        from app.api.outfits import visible_outfits
        row = self.db.get(Outfit, outfit['id'])
        self.assertEqual(row.intent_constraints_json['budget_max'], 20000)
        self.db.get(Product, outfit['items']['top']).price = 12000
        with self.assertRaises(intents.IntentError):
            intents.save_intent_outfit(self.db, self.user, intent.id, outfit['id'], expected_version=1)
        self.assertEqual(visible_outfits(self.db, self.user, [row]), [])

    def test_replace_without_alternative_preserves_original_version_and_items(self):
        self.seed_outfit()
        intent = self.create('Образ до 20000 без платья')
        before = intents.intent_response(self.db, self.user, intent, remember=True)['outfits'][0]
        snapshot = deepcopy(intent.context_json)
        with self.assertRaises(intents.IntentError):
            intents.replace_outfit_item(self.db, self.user, intent.id, before['id'], expected_version=1, slot='top')
        self.assertEqual(intent.version, 1)
        self.assertEqual(intent.context_json, snapshot)

    def test_intent_migration_upgrade_and_downgrade(self):
        import importlib.util
        from pathlib import Path
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        from sqlalchemy import text, inspect
        engine = create_engine('sqlite:///:memory:')
        self.addCleanup(engine.dispose)
        source = Path(__file__).resolve().parents[1]/'alembic/versions/20261008_0024_shopping_intents.py'
        spec = importlib.util.spec_from_file_location('intent_migration', source)
        migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
        with engine.begin() as connection:
            connection.execute(text('CREATE TABLE users (id VARCHAR(36) PRIMARY KEY)'))
            connection.execute(text('CREATE TABLE outfits (id VARCHAR(36) PRIMARY KEY)'))
            with Operations.context(MigrationContext.configure(connection)): migration.upgrade()
            self.assertIn('shopping_intents', inspect(connection).get_table_names())
            self.assertIn('intent_constraints_json', {c['name'] for c in inspect(connection).get_columns('outfits')})
            with Operations.context(MigrationContext.configure(connection)): migration.downgrade()
            self.assertNotIn('shopping_intents', inspect(connection).get_table_names())

    def test_task_feedback_is_price_or_session_only_and_never_a_style_dislike(self):
        product = self.product()
        intent = self.create()
        intents.intent_response(self.db, self.user, intent, remember=True)
        taste = self.db.scalar(select(TasteProfile).where(TasteProfile.user_id == self.user.id))
        snapshot = deepcopy((taste.style_weights, taste.color_weights, taste.profile_version))
        updated = intents.feedback(self.db, self.user, intent.id, product_id=product.id,
            reason='not_for_context', expected_version=1)
        result = intents.intent_response(self.db, self.user, updated, remember=True)
        self.assertEqual(result['products'], [])
        self.assertEqual(snapshot, (taste.style_weights, taste.color_weights, taste.profile_version))
        self.assertEqual(self.db.scalar(select(func.count()).select_from(UserTasteFeature)), 0)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(__import__('app.models', fromlist=['RecommendationEventV2']).RecommendationEventV2)), 0)
        intent = self.create(request_id=uuid4().hex)
        intents.intent_response(self.db, self.user, intent, remember=True)
        updated = intents.feedback(self.db, self.user, intent.id, product_id=product.id,
            reason='too_expensive', expected_version=1)
        self.assertEqual(updated.constraints_json['budget_max'], 4800)
        self.assertEqual(snapshot, (taste.style_weights, taste.color_weights, taste.profile_version))
        self.db.flush()
        from app.services.intent_metrics import summary
        metrics = summary(self.db)
        self.assertEqual(metrics['intent_funnel']['dislike_rate'], 1)
        self.assertEqual(metrics['recommendation_funnel']['dislike_rate'], 1)
        self.assertEqual(metrics['recommendation_funnel']['exposed_products'], 2)

    def test_colour_feedback_updates_colour_but_not_material_or_brand(self):
        product = self.product(colors=['black'])
        intent = self.create()
        intents.intent_response(self.db, self.user, intent, remember=True)
        intents.feedback(self.db, self.user, intent.id, product_id=product.id,
            reason='dont_like_color', expected_version=1)
        kinds = set(self.db.scalars(select(UserTasteFeature.feature_type)))
        self.assertTrue(kinds <= {'item', 'color'})
        self.assertIn('color', kinds)
        from app.models import RecommendationEventV2
        metadata = self.db.scalar(select(RecommendationEventV2)).meta_json
        self.assertEqual(metadata['reason'], 'dont_like_color')
        self.assertEqual(metadata['observed_price'], 6000)

    def test_task_view_is_owned_versioned_and_does_not_train_taste(self):
        from app.services.product_event_service import record_product_event
        from app.schemas.product_event import EventRequest
        from fastapi import HTTPException
        product = self.product()
        intent = self.create(); intents.intent_response(self.db, self.user, intent, remember=True)
        meta = {'surface': 'shopping_intent', 'intent_id': intent.id, 'intent_version': intent.version}
        record_product_event(self.db, self.user, EventRequest(product_id=product.id, event_type='view', meta=meta), commit=False)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(UserTasteFeature)), 0)
        other = self.product()
        with self.assertRaises(HTTPException):
            record_product_event(self.db, self.user, EventRequest(product_id=other.id, event_type='view', meta=meta), commit=False)

    def test_metrics_have_explicit_denominators_and_exclude_free_text(self):
        from app.services.intent_metrics import summary
        from app.models import MetricEvent
        self.product()
        self.product(available_sizes=['44'])
        self.product(availability_status='unknown')
        intent = self.create()
        intents.intent_response(self.db, self.user, intent, remember=True)
        self.db.flush()
        result = summary(self.db)
        self.assertEqual(result['availability_pass_rate'], .6667)
        self.assertEqual(result['size_pass_rate'], .5)
        self.assertEqual(result['invalid_recommendation_rate'], 0)
        for metric in self.db.scalars(select(MetricEvent)):
            self.assertNotIn('text', metric.meta_json)
            self.assertNotIn('free_text', metric.meta_json)

    def test_dress_hard_budget_excludes_12000_before_ranking(self):
        wanted = self.product(title='Платье', category='платья', price=7000)
        rejected = self.product(title='Платье вечернее', category='платья', price=12000)
        scored = []
        with patch.object(intents.AffinityScorer, 'score', side_effect=lambda p: scored.append(p.id) or .5):
            result = intents.intent_response(self.db, self.user, self.create('Хочу платье до 8000'))
        self.assertEqual(scored, [wanted.id])
        self.assertNotIn(rejected.id, [r['product']['id'] for r in result['products']])

    def test_missing_favourite_brand_still_uses_product_dna(self):
        from app.services.user_twin_service import DNA_TASTE_PREFIX
        taste = self.db.scalar(select(TasteProfile).where(TasteProfile.user_id == self.user.id))
        taste.brand_weights = {'missing-brand': 20}
        self.db.add(UserTasteFeature(id=str(uuid4()), user_id=self.user.id, feature_type='material',
            feature_value=DNA_TASTE_PREFIX+'cotton', preference_score=.9, confidence=.9,
            positive_count=10, negative_count=0))
        cotton = self.product(title='Хлопковые брюки', brand='Other Brand', feed_raw_json={'size_system': 'RU', 'material': 'cotton'})
        self.product(title='Льняные брюки', brand='Another', feed_raw_json={'size_system': 'RU', 'material': 'linen'})
        result = intents.intent_response(self.db, self.user, self.create('Брюки до 7000'))
        self.assertEqual(result['products'][0]['product']['id'], cotton.id)
        self.assertIn('хлопок', ' '.join(result['products'][0]['reasons']))

    def test_read_preserves_issued_items_without_ranking_and_revalidates_stock(self):
        product = self.product()
        intent = self.create()
        issued = intents.intent_response(self.db, self.user, intent, remember=True)
        self.product(price=5000)
        with patch.object(intents, 'item_results', side_effect=AssertionError('GET must not rank')):
            read = intents.intent_response(self.db, self.user, intent)
        self.assertEqual([r['product']['id'] for r in read['products']],
            [r['product']['id'] for r in issued['products']])
        product.is_available = 0
        read = intents.intent_response(self.db, self.user, intent)
        self.assertEqual(read['products'], [])
        self.assertEqual(intent.context_json['product_ids'], [product.id])

    def test_read_revalidates_outfit_snapshot_without_rebuilding_candidates(self):
        self.seed_outfit()
        intent = self.create('Образ до 20000 без платья')
        issued = intents.intent_response(self.db, self.user, intent, remember=True)
        with patch.object(intents, 'build_intent_outfits', side_effect=AssertionError('GET must not build')):
            read = intents.intent_response(self.db, self.user, intent)
        self.assertEqual([o['id'] for o in read['outfits']], [o['id'] for o in issued['outfits']])
        shoe = self.db.get(Product, issued['outfits'][0]['items']['shoes'])
        shoe.last_seen_in_feed_at = datetime.now(timezone.utc)-timedelta(hours=73)
        read = intents.intent_response(self.db, self.user, intent)
        self.assertNotIn(issued['outfits'][0]['id'], [o['id'] for o in read['outfits']])

    def test_soft_colour_ranks_final_outfits_without_excluding_other_colours(self):
        self.product(title='Брюки',price=5000)
        black=self.product(id='00000000-0000-0000-0000-000000000000',title='Рубашка',category='рубашки',price=5000)
        white=self.product(id='ffffffff-ffff-ffff-ffff-ffffffffffff',title='Рубашка',category='рубашки',price=5000,
            feed_raw_json={'size_system':'RU','color':'white'})
        self.product(title='Лоферы',category='обувь',price=4000,available_sizes=['38'],feed_raw_json={'size_system':'EU'})
        intent=self.create('Образ до 20000, желательно чёрное')
        # Keep Taste tied while exercising soft preferences inside the shared
        # Context component; mocking the final score would erase that factor.
        with patch('app.services.personalization_scoring._base_affinity',return_value=.5):
            response=intents.intent_response(self.db,self.user,intent,remember=True)
        self.assertEqual(response['outfits'][0]['items']['top'],black.id)
        self.assertIsNone(intent_product_problem(white,self.fit,IntentSpec.model_validate(intent.constraints_json)))

    def test_unknown_sizes_do_not_parse_semantic_dna_or_score(self):
        good=self.product()
        for _ in range(15): self.product(available_sizes=['46'])
        parsed=[]
        real=intents.intent_facts
        with patch.object(intents,'intent_facts',side_effect=lambda p: parsed.append(p.id) or real(p)):
            result=intents.intent_response(self.db,self.user,self.create('Брюки до 7000'),remember=True)
        self.assertEqual(parsed,[good.id])
        self.assertEqual([r['product']['id'] for r in result['products']],[good.id])

    def test_missing_required_shoe_role_never_scores_or_loads_clothing(self):
        from sqlalchemy import event
        from app.services import intent_outfits
        top = self.product(title='Рубашка', category='рубашки')
        bottom = self.product()
        shoe = self.product(title='Лоферы', category='обувь', available_sizes=['37'],
            feed_raw_json={'size_system': 'EU'})
        self.db.commit()
        top_id, bottom_id, shoe_id = top.id, bottom.id, shoe.id
        user_id = self.user.id
        self.db.expunge_all()
        user = self.db.get(User, user_id)
        loaded = []
        def on_load(row, context): loaded.append(row.id)
        event.listen(Product, 'load', on_load)
        try:
            stats = {}
            with patch.object(intent_outfits.AffinityScorer, 'score', side_effect=AssertionError('Cannot rank an incomplete outfit')):
                result = intent_outfits.build_intent_outfits(self.db, user,
                    parse_intent('Образ в офис до 20000'), intent_id=str(uuid4()), version=1, stats=stats)
            self.assertEqual(result, [])
            self.assertNotIn(top_id, loaded)
            self.assertNotIn(bottom_id, loaded)
            self.assertEqual(loaded, [shoe_id])
            self.assertEqual((stats['catalog_count'], stats['available_count'], stats['size_checked'], stats.get('size_passed', 0)), (3, 3, 1, 0))
        finally:
            event.remove(Product, 'load', on_load)

    def test_retry_keeps_same_snapshot_and_does_not_multiply_generation(self):
        from app.models import MetricEvent
        product = self.product()
        intent = self.create(request_id='one-logical-request')
        first = intents.intent_response(self.db, self.user, intent, remember=True)
        self.product(price=5000)
        with patch.object(intents, 'item_results', side_effect=AssertionError('Retry must not rerank')):
            second = intents.intent_response(self.db, self.user, self.create(request_id='one-logical-request'), remember=True)
        self.assertEqual([r['product']['id'] for r in first['products']],
                         [r['product']['id'] for r in second['products']])
        self.assertEqual([r['reasons'] for r in first['products']],
                         [r['reasons'] for r in second['products']])
        product.price = 8000
        self.assertEqual(intents.intent_response(self.db, self.user, intent, remember=True)['products'], [])
        self.assertEqual(intent.context_json['product_ids'], [product.id])
        self.db.flush()
        self.assertEqual(self.db.scalar(select(func.count()).select_from(MetricEvent).where(MetricEvent.name == 'intent_results_generated')), 1)

    def test_outfit_rates_use_visible_unique_outfits_and_ignore_wrong_owner(self):
        from app.models import MetricEvent, RecommendationEventV2
        from app.services.intent_metrics import emit, summary
        self.seed_outfit()
        self.product(title='Блузка', category='блузки', price=4500)
        self.product(title='Брюки прямые', price=5000)
        intent = self.create('Образ в офис до 20000 без платья')
        result = intents.intent_response(self.db, self.user, intent, remember=True)
        self.assertGreaterEqual(len(result['outfits']), 2)
        first, second = result['outfits'][:2]
        from app.services.product_event_service import record_product_event
        from app.schemas.product_event import EventRequest
        for outfit, action in ((first, 'view'), (first, 'view'), (second, 'affiliate_click')):
            record_product_event(self.db, self.user, EventRequest(product_id=outfit['items']['top'], event_type=action,
                meta={'surface': 'shopping_intent', 'intent_id': intent.id, 'intent_version': 1,
                      'intent_outfit_id': outfit['id']}), commit=False)
        intents.save_intent_outfit(self.db, self.user, intent.id, first['id'], expected_version=1)
        intents.save_intent_outfit(self.db, self.user, intent.id, first['id'], expected_version=1)
        intents.replace_outfit_item(self.db, self.user, intent.id, first['id'], expected_version=1, slot='shoes')
        # A forged owner or unknown outfit must not enter a rate numerator.
        other = User(id=str(uuid4()), email='forged-metrics@example.test', password_hash='unused')
        self.db.add(other); self.db.flush()
        emit(self.db, other.id, 'outfit_saved', intent_id=intent.id, outfit_id=second['id'])
        emit(self.db, self.user.id, 'outfit_saved', intent_id=intent.id, outfit_id='never-generated')
        self.db.add(RecommendationEventV2(id=str(uuid4()), user_id=other.id, product_id=first['items']['top'],
            outfit_id=first['id'], event_type='affiliate_click',
            meta_json={'surface': 'shopping_intent', 'intent_id': intent.id}))
        self.db.flush()
        report = summary(self.db)
        self.assertEqual(report['outfit_funnel']['exposed_outfits'], 2)
        self.assertEqual(report['outfit_funnel']['save_rate'], .5)
        self.assertEqual(report['outfit_funnel']['merchant_click_rate'], .5)
        self.assertEqual(report['outfit_funnel']['replace_item_rate'], .5)
        self.assertEqual(report['intent_funnel']['save_rate'], 1)
        self.assertEqual(report['invalid_recommendation_rate'], 0)
        self.assertIn('availability_filtered', report['events'])
        self.assertIn('size_filtered', report['events'])
        self.assertIn('recommendation_explained', report['events'])
        self.assertIn('brand_affinity_updated', report['events'])

    def test_forged_outfit_attribution_is_rejected_before_event_and_taste_write(self):
        from app.services.product_event_service import record_product_event
        from app.schemas.product_event import EventRequest
        from app.models import RecommendationEventV2
        from fastapi import HTTPException
        self.seed_outfit()
        intent = self.create('Образ в офис до 20000')
        result = intents.intent_response(self.db, self.user, intent, remember=True)
        with self.assertRaises(HTTPException) as error:
            record_product_event(self.db, self.user, EventRequest(event_type='affiliate_click',
                product_id=result['outfits'][0]['items']['top'], meta={'surface': 'shopping_intent',
                    'intent_id': intent.id, 'intent_version': 1, 'intent_outfit_id': str(uuid4())}), commit=False)
        self.assertEqual(error.exception.status_code, 404)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(RecommendationEventV2)), 0)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(UserTasteFeature)), 0)

    def test_empty_metric_denominators_are_unknown_and_event_contract_is_explicit(self):
        from app.services.intent_metrics import summary, EVENT_NAMES, emit
        report = summary(self.db)
        self.assertIsNone(report['outfit_funnel']['save_rate'])
        self.assertIsNone(report['recommendation_funnel']['like_rate'])
        self.assertIsNone(report['intent_funnel']['save_rate'])
        expected = {'intent_created', 'intent_updated', 'intent_completed', 'intent_abandoned',
            'recommendation_explained', 'outfit_generated', 'outfit_saved', 'outfit_item_replaced',
            'size_filtered', 'availability_filtered', 'brand_affinity_updated', 'reason_dislike'}
        self.assertTrue(expected <= set(EVENT_NAMES.values()))
        with self.assertRaises(ValueError): emit(self.db, self.user.id, 'unrecognized')
        with self.assertRaises(ValueError): emit(self.db, self.user.id, 'created', text='private request')

    def test_role_changed_after_thin_read_cannot_fill_an_old_outfit_slot(self):
        from app.services import intent_outfits
        top = self.product(title='Рубашка', category='рубашки', price=4000)
        self.product(title='Брюки', price=4000)
        self.product(title='Лоферы', category='обувь', price=4000,
            available_sizes=['38'], feed_raw_json={'size_system': 'EU'})
        execute = self.db.execute
        def change_after_index(query, *args, **kwargs):
            result = execute(query, *args, **kwargs)
            # Simulate a concurrent importer repairing a mislabelled garment.
            if len(query.selected_columns) == 5:
                indexed = list(result)
                top.title = 'Куртка'
                return indexed
            return result
        with patch.object(self.db, 'execute', side_effect=change_after_index):
            result = intent_outfits.build_intent_outfits(self.db, self.user,
                parse_intent('Образ до 20000'), intent_id=str(uuid4()), version=1)
        self.assertEqual(result, [])

    def test_rejected_shoe_is_not_a_replacement_and_preserves_current_outfit(self):
        self.seed_outfit()
        intent = self.create('Образ до 20000 без платья')
        first = intents.intent_response(self.db, self.user, intent, remember=True)['outfits'][0]
        rejected = first['items']['shoes']
        updated = intents.feedback(self.db, self.user, intent.id, expected_version=1,
            product_id=rejected, reason='not_for_context')
        second = intents.intent_response(self.db, self.user, updated, remember=True)['outfits'][0]
        self.assertNotEqual(second['items']['shoes'], rejected)
        context_before = deepcopy(intent.context_json)
        with self.assertRaises(intents.IntentError):
            intents.replace_outfit_item(self.db, self.user, intent.id, second['id'],
                expected_version=2, slot='shoes')
        self.assertEqual(intent.version, 2)
        self.assertEqual(intent.context_json, context_before)
        read = intents.intent_response(self.db, self.user, intent)
        self.assertEqual([o['id'] for o in read['outfits']], [second['id']])
