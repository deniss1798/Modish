"""Read the real clone with synthetic profiles; roll back every test write."""
from datetime import datetime, timezone
import json, os, sys
from time import perf_counter
from uuid import uuid4
sys.path.insert(0, '/app')
from sqlalchemy import select, func, text
from sqlalchemy.engine import make_url
assert make_url(os.environ['DATABASE_URL']).host == 'modish_foundation_test_pg'
assert make_url(os.environ['DATABASE_URL']).database == 'modish_test'
from app.db import SessionLocal
from app.models import User, Product, FitProfile
from app.services import activation_service as activation, shopping_intent_service as intents
from app.services.intent_eligibility import intent_product_problem
from app.schemas.shopping_intent import IntentSpec
from app.services.intent_metrics import summary

report = {'scope': 'isolated_real_catalogue', 'checked_at': datetime.now(timezone.utc).isoformat(),
    'archive_sha256': os.environ['AUDIT_ARCHIVE_SHA256'], 'image_id': os.environ['AUDIT_IMAGE_ID'],
    'stock_timestamps_modified': False, 'synthetic_writes_rolled_back': True, 'queries': []}
with SessionLocal() as db:
    db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ'))
    count = db.scalar(select(func.count()).select_from(Product))
    user = User(id=str(uuid4()), email='latency-'+uuid4().hex+'@example.test', password_hash='disabled-test-account')
    db.add(user); db.flush()
    activation.configure(db, user, age_group='25-34', height=168, clothing_size='L',
        clothing_size_system='LETTER', shoe_size='38', shoe_size_system='EU',
        styles=['casual', 'minimalism'], price_segment='mid')
    fit = db.scalar(select(FitProfile).where(FitProfile.user_id == user.id))
    # This measures intent service latency, not onboarding completion or taste.
    for query in ('Брюки до 7000', 'Образ в офис до 20000', 'Образ на свадьбу до 20000 без платья и каблуков'):
        timings, snapshots = [], []
        for _ in range(3):
            start = perf_counter()
            intent = intents.create_intent(db, user, text=query, request_id=uuid4().hex)
            response = intents.intent_response(db, user, intent, remember=True)
            timings.append(perf_counter()-start)
            spec = IntentSpec.model_validate(response['constraints'])
            ids = {r['product']['id'] for r in response['products']}
            ids.update(pid for outfit in response['outfits'] for pid in outfit['items'].values())
            assert all(intent_product_problem(db.get(Product, pid), fit, spec) is None for pid in ids)
            snapshots.append({'product_count': len(response['products']), 'outfit_count': len(response['outfits'])})
        assert all(row == snapshots[0] for row in snapshots)
        report['queries'].append({'query_kind': spec.intent_type, 'occasion': spec.occasion,
            **snapshots[0], 'seconds': timings, 'median_seconds': sorted(timings)[1]})
        print('latency_query_completed '+str(len(report['queries'])), file=sys.stderr, flush=True)
    db.flush()
    metrics = summary(db)
    assert metrics['intent_funnel']['cohort_intents'] == 9
    if 'outfit_funnel' in metrics:
        assert metrics['outfit_funnel']['exposed_outfits'] == 0
        assert metrics['outfit_funnel']['save_rate'] is None
        assert metrics['recommendation_funnel']['exposed_products'] == 0
    report['postgresql_metrics_query_passed'] = True
    report['catalogue_product_count'] = count
    report['test_cohort_intents'] = metrics['intent_funnel']['cohort_intents']
    db.rollback()
    assert db.scalar(select(func.count()).select_from(Product)) == count
    assert db.get(User, user.id) is None
print(json.dumps(report, ensure_ascii=True))
