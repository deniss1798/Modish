"""60 distinct real-catalogue swipes and expired legacy cooldowns on the clone."""
from datetime import datetime, timedelta, timezone
import json
import logging
import os
import secrets
import sys
from uuid import uuid4

sys.path.insert(0, '/app')
from sqlalchemy import select, update
from sqlalchemy.engine import make_url

url = make_url(os.environ['DATABASE_URL'])
assert url.host == 'modish_foundation_test_pg' and url.database == 'modish_test'
import dotenv
dotenv.load_dotenv = lambda *args, **kwargs: False
from fastapi.testclient import TestClient
from app.main import app
from app.db import SessionLocal
from app.models import Product, RecommendationEventV2, UserProductState
from app.services.product_identity import product_identity_keys

logging.disable(logging.CRITICAL)
report = {'scope': 'isolated_clone', 'target': 60, 'unique_cards': 0,
          'client_restarts': 0, 'expired_legacy_cooldowns': True, 'pages': [],
          'result': 'running', 'checked_at': datetime.now(timezone.utc).isoformat()}


def request(client, method, path, **kwargs):
    response = client.request(method, path, **kwargs)
    assert response.status_code == 200, f'{path}: HTTP_{response.status_code}'
    return response.json()


with TestClient(app) as client:
    auth = request(client, 'POST', '/auth/register', json={
        'email': f'swipe-regression-{uuid4().hex}@example.com',
        'password': secrets.token_urlsafe(24)})
    headers = {'Authorization': 'Bearer ' + auth['access_token']}
    uid = request(client, 'GET', '/users/me', headers=headers)['id']
    request(client, 'POST', '/onboarding/preferences', headers=headers, json={
        'age_group': '25-34', 'clothing_size': 'M', 'clothing_size_system': 'LETTER',
        'shoe_size': '38', 'shoe_size_system': 'EU',
        'styles': ['casual', 'minimalism'], 'price_segment': 'mass'})
    deck = request(client, 'GET', '/onboarding/style-swipe', headers=headers)['products']
    assert len(deck) >= 15, 'insufficient_real_catalogue_for_onboarding'
    for product in deck[:15]:
        request(client, 'POST', '/onboarding/style-swipe', headers=headers,
                json={'product_id': product['id'], 'reaction': 'like'})
    request(client, 'POST', '/onboarding/complete', headers=headers)

seen = set()
ids = []
for page in range(3):
    # A new client and no exclude_ids: only persisted server history can help.
    with TestClient(app) as client:
        rows = request(client, 'GET', '/feed?limit=20', headers=headers)
        report['pages'].append(len(rows))
        if not rows:
            break
        with SessionLocal() as db:
            for row in rows:
                pid = row['product']['id']
                keys = product_identity_keys(db.get(Product, pid))
                assert not keys & seen, 'duplicate_visible_model_after_feedback'
                seen.update(keys)
                ids.append(pid)
        for row in rows:
            request(client, 'POST', '/recommendations/events', headers=headers,
                    json={'product_id': row['product']['id'], 'event_type': 'skip',
                          'meta': {'surface': 'feed'}})
    # Simulate the old one-day skip cooldown expiring, limited to this test user.
    with SessionLocal() as db:
        past = datetime.now(timezone.utc) - timedelta(days=2)
        db.execute(update(UserProductState).where(UserProductState.user_id == uid)
                   .values(hidden_until=past))
        db.execute(update(RecommendationEventV2).where(
            RecommendationEventV2.user_id == uid,
            RecommendationEventV2.event_type == 'skip').values(created_at=past))
        db.commit()
    report['client_restarts'] += 1
    print(json.dumps({'progress': 'swipe_page', 'page': page + 1,
                      'unique_cards': len(ids)}), flush=True)

with TestClient(app) as client, SessionLocal() as db:
    # One more read after all swipes also must not restore an older variant.
    rows = request(client, 'GET', '/feed?limit=20', headers=headers)
    for row in rows:
        assert not product_identity_keys(db.get(Product, row['product']['id'])) & seen
report.update(result='passed', unique_cards=len(ids), target_reached=len(ids) >= 60,
              final_refill=len(rows), duplicate_models=0)
print(json.dumps(report), flush=True)
