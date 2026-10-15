"""Real-catalogue P1/P2 smoke, restricted to the isolated test database."""
from datetime import datetime, timezone
import json
import os
import sys
from time import perf_counter
from uuid import uuid4

sys.path.insert(0, '/app')
from sqlalchemy import select
from sqlalchemy.engine import make_url
url = make_url(os.environ['DATABASE_URL'])
assert url.host == 'modish_foundation_test_pg' and url.database == 'modish_test'
from app.db import SessionLocal
from app.models import FitProfile, Product, User
from app.services import activation_service as activation
from app.services.catalog_filters import CatalogFilters
from app.services.product_search import search_conditions, personalized_search
from app.services.catalog.stock_freshness import fresh_stock_conditions
from app.services.mie_scoring import load_user_taste_feature_map
from app.api.recommendations import EventRequest, record_product_event
from app.services.catalog.product_dna import reliable_taste_features, RULE_VERSION

report = {'scope': 'isolated_clone', 'checked_at': datetime.now(timezone.utc).isoformat(),
    'rules_version': RULE_VERSION, 'image_id': os.environ.get('MODISH_SMOKE_IMAGE_ID'),
    'archive_sha256': os.environ.get('MODISH_SMOKE_ARCHIVE_SHA256'), 'checks': []}
with SessionLocal() as db:
    user = User(id=str(uuid4()), email=f'dna-smoke-{uuid4().hex}@example.com', password_hash='disabled-test-only')
    db.add(user); db.flush()
    session = activation.configure(db, user, gender_target='womenswear', age_group='25-34', clothing_size='M', shoe_size='38',
        clothing_size_system='LETTER', shoe_size_system='EU', styles=['casual', 'minimalism'], price_segment='mass')
    pool = activation.candidate_pool(db, user, session)
    assert len(pool) >= 20, 'Insufficient honest letter-size catalogue'
    report['initial_pool'] = {'cards': len(pool), 'context_evidence': {
        dim: sum(bool(reliable_taste_features(p, dimensions=(dim,))) for p in pool)
        for dim in ('style', 'occasion', 'formality')},
        'scope': 'eligible candidate pool for one M / EU 38 / mass profile, not entire catalogue'}
    fit = db.scalar(select(FitProfile).where(FitProfile.user_id == user.id))
    cotton = [p for p in pool if 'cotton' in reliable_taste_features(p, dimensions=('material',)).get('material', set())]
    assert cotton, 'Need real cotton evidence'
    for p in cotton[:10]:
        record_product_event(db, user, EventRequest(product_id=p.id, event_type='like',
            meta={'surface': 'style_swipe'}), commit=False)
    session.completed_at = datetime.now(timezone.utc); db.flush()
    before = load_user_taste_feature_map(db, user_id=user.id, as_of=session.completed_at)
    for scenario, category in [('daily', None), ('office', None), ('evening', None), ('daily', 'outerwear')]:
        started = perf_counter()
        rows = activation.personalized_feed(db, user, session, scenario=scenario,
            filters=CatalogFilters(categories=(category,)) if category else None)
        assert rows, f'Empty qualified feed: {scenario}/{category}'
        assert all(activation.product_ok(row.product, fit, session) for row in rows)
        report['checks'].append({'scenario': scenario, 'category': category, 'cards': len(rows),
            'seconds': round(perf_counter()-started, 3), 'ids': [r.product.id for r in rows]})
    started = perf_counter()
    query = select(Product).where(*fresh_stock_conditions(), Product.is_active == 1,
        Product.gender_target == 'womenswear', *search_conditions('рубашка'))
    search = personalized_search(db, user, session, query, limit=20)
    assert search and all(activation.product_ok(p, fit, session) for p in search)
    report['search'] = {'query': 'рубашка', 'cards': len(search), 'seconds': round(perf_counter()-started, 3)}
    assert before == load_user_taste_feature_map(db, user_id=user.id, as_of=session.completed_at)
    report['intent_did_not_modify_taste'] = True
    # Roll back the test user and events, leaving the imported catalogue intact.
    db.rollback()
report['result'] = 'passed'
print(json.dumps(report), flush=True)
