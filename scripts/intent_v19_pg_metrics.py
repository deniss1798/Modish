"""Positive outfit metric contract in an isolated PostgreSQL transaction."""
from datetime import datetime, timezone
import json, os, sys
from uuid import uuid4
sys.path[:0] = ['/app', '/app/tests']
from sqlalchemy import select, func, text
from sqlalchemy.engine import make_url
assert make_url(os.environ['DATABASE_URL']).host == 'modish_foundation_test_pg'
assert make_url(os.environ['DATABASE_URL']).database == 'modish_test'
from app.db import SessionLocal
from app.models import User, Product, FitProfile, TasteProfile, UserTasteFeature, RecommendationEventV2
from app.services import activation_service as activation
from app.services.intent_metrics import summary
from app.services import shopping_intent_service as intents
from copy import deepcopy
import test_shopping_intent as fixtures
with SessionLocal() as db:
    db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ'))
    before = db.scalar(select(func.count()).select_from(Product))
    user = User(id=str(uuid4()), email='pg-metric-'+uuid4().hex+'@example.test', password_hash='disabled-test-account')
    uid = user.id
    db.add(user); db.flush()
    activation.configure(db, user, gender_target='womenswear', age_group='25-34', height=168, clothing_size='48',
        clothing_size_system='RU', shoe_size='38', shoe_size_system='EU',
        styles=['casual', 'minimalism'], price_segment='mid')
    case = fixtures.ShoppingIntentTests()
    case.db, case.user = db, user
    case.fit = db.scalar(select(FitProfile).where(FitProfile.user_id == user.id))
    # This fixture supplies explicit source-size evidence; it does not pretend
    # real merchants gained known sizes or renew their stock observations.
    savepoint = db.begin_nested()
    case.test_outfit_rates_use_visible_unique_outfits_and_ignore_wrong_owner()
    outfit_metrics = summary(db)['outfit_funnel']
    savepoint.rollback()
    savepoint = db.begin_nested()
    case.seed_outfit()
    task = case.create('Образ до 20000 без платья')
    first = intents.intent_response(db, user, task, remember=True)['outfits'][0]
    db.flush()  # API request boundary, without committing any test write.
    updated = intents.feedback(db, user, task.id, expected_version=1,
        product_id=first['items']['shoes'], reason='not_for_context')
    db.flush()
    second = intents.intent_response(db, user, updated, remember=True)['outfits'][0]
    db.flush()
    context_before = deepcopy(task.context_json)
    try:
        intents.replace_outfit_item(db, user, task.id, second['id'], expected_version=2, slot='shoes')
    except intents.IntentError:
        pass
    else:
        raise AssertionError('A rejected shoe must not be an alternative')
    assert task.version == 2 and task.context_json == context_before
    assert [o['id'] for o in intents.intent_response(db, user, task)['outfits']] == [second['id']]
    savepoint.rollback()
    savepoint = db.begin_nested()
    product = case.product()
    taste=db.scalar(select(TasteProfile).where(TasteProfile.user_id == uid))
    taste_before=deepcopy((taste.style_weights,taste.color_weights,taste.brand_weights,taste.category_weights,taste.profile_version))
    before_twin = db.scalar(select(func.count()).select_from(UserTasteFeature).where(UserTasteFeature.user_id == uid))
    before_events = db.scalar(select(func.count()).select_from(RecommendationEventV2).where(RecommendationEventV2.user_id == uid))
    for reason in ('not_for_context', 'too_expensive'):
        intent = case.create()
        response = intents.intent_response(db, user, intent, remember=True)
        assert product.id in [r['product']['id'] for r in response['products']]
        db.flush()
        intents.feedback(db, user, intent.id, expected_version=1, product_id=product.id, reason=reason)
    db.flush()
    reason_metrics = summary(db)
    assert reason_metrics['intent_funnel']['dislike_rate'] == 1
    assert reason_metrics['recommendation_funnel']['dislike_rate'] == 1
    assert reason_metrics['recommendation_funnel']['exposed_products'] == 2
    assert db.scalar(select(func.count()).select_from(UserTasteFeature).where(UserTasteFeature.user_id == uid)) == before_twin
    assert db.scalar(select(func.count()).select_from(RecommendationEventV2).where(RecommendationEventV2.user_id == uid)) == before_events
    assert taste_before == (taste.style_weights,taste.color_weights,taste.brand_weights,taste.category_weights,taste.profile_version)
    report = {'scope': 'isolated_postgresql_synthetic_contract_not_real_coverage',
        'checked_at': datetime.now(timezone.utc).isoformat(), 'image_id': os.environ['AUDIT_IMAGE_ID'],
        'checks_passed': True, 'real_stock_timestamps_modified': False,
        'outfit_funnel': outfit_metrics, 'task_only_dislike_rate': 1,
        'task_only_taste_unchanged': True, 'replacement_respects_rejected_skus': True,
        'test_writes_rolled_back': True}
    savepoint.rollback()
    db.rollback()
    assert db.scalar(select(func.count()).select_from(Product)) == before
    assert db.get(User, uid) is None
print(json.dumps(report))
