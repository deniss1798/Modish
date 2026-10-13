"""Read real catalog through the new filter logic; never persist test profiles."""
import json
from types import SimpleNamespace
from unittest.mock import patch
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.db import engine
from app.services import activation_service as activation
from app.services.catalog_filters import CatalogFilters


with Session(engine, autoflush=False) as db:
    db.execute(text('SET TRANSACTION READ ONLY'))
    assert db.scalar(text('SHOW transaction_read_only')) == 'on'
    user = SimpleNamespace(id='00000000-0000-4000-a000-000000000013')
    profile = SimpleNamespace(style_preferences=['casual', 'minimalism'])
    fit = SimpleNamespace(gender_target='womenswear', budget_max=25000, clothing_size='M',
        interest_categories=['рубашки'])
    taste = SimpleNamespace(liked_categories=['рубашки'], category_weights={'рубашки': 10},
        liked_styles=['casual'], style_weights={}, color_weights={}, brand_weights={})
    session = SimpleNamespace(shoe_size='38', swipes_json={}, issued_feed_ids=[])
    report = {'transaction_read_only': True, 'categories': []}
    with patch.object(activation, '_preferences', return_value=(profile, fit, taste)):
        for key in ['shoes', 'outerwear']:
            filters = CatalogFilters(categories=(key,))
            pool = activation.candidate_pool(db, user, session, restrict_categories=True, filters=filters)
            rows = activation.personalized_feed(db, user, session, limit=20, filters=filters)
            assert pool, f'No real eligible products: {key}'
            assert len(rows) == min(20, len(pool)), key
            assert all(filters.matches(row.product) and activation.product_ok(row.product, fit, session) for row in rows)
            report['categories'].append({'category': key, 'eligible_pool': len(pool), 'page': len(rows)})
    db.rollback()
    report['result'] = 'passed'
    print(json.dumps(report))
