"""Authenticated HTTP discovery smoke in a named isolated PostgreSQL copy."""
import argparse
import json
import logging
import os
from pathlib import Path
import sys
from unittest.mock import patch
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--isolated-write-test', required=True, action='store_true')
    parser.add_argument('--expected-host', required=True)
    parser.add_argument('--expected-database', required=True)
    args = parser.parse_args()
    from sqlalchemy import delete, func, select, text
    from sqlalchemy.engine import make_url
    url = make_url(os.environ['DATABASE_URL'])
    if (not __debug__ or url.host != args.expected_host or url.database != args.expected_database
            or url.get_backend_name() != 'postgresql' or url.database in {'modish', 'postgres'}
            or not any(value in url.database for value in ('test', 'clone', 'restore'))):
        raise ValueError('isolated_database_guard')
    logging.disable(logging.CRITICAL)
    os.environ.update(APP_ENV='test', PYTHON_DOTENV_DISABLED='1')
    sys.path.insert(0, '/app')
    source_root = Path('/app') if Path('/app/app').is_dir() else Path(__file__).resolve().parents[1] / 'backend'
    sys.path.insert(0, str(source_root))
    with patch('dotenv.load_dotenv'):
        from fastapi.testclient import TestClient
        from app.db import Base, SessionLocal, engine
        from app.main import app
        from app.models import User, FitProfile, StyleProfile, TasteProfile, RecommendationEventV2
        from app.api.deps import create_access_token
    assert engine.url == url
    owner = str(uuid4())
    checks = []
    with SessionLocal() as db:
        assert db.scalar(text('SELECT current_database()')) == args.expected_database
        assert db.scalar(text('SELECT version_num FROM alembic_version')) == '20261013_0025'
        before = {model.__tablename__: db.scalar(select(func.count()).select_from(model))
                  for model in (User, TasteProfile, RecommendationEventV2)}
        db.add(User(id=owner, email=f'http-smoke-{uuid4().hex}@example.invalid', password_hash='unused'))
        db.flush()
        db.add(FitProfile(id=str(uuid4()), user_id=owner, height_cm=170,
                          gender_target='womenswear', clothing_size=None, budget_max=25000))
        db.add(StyleProfile(id=str(uuid4()), user_id=owner, profile_json={}))
        db.commit()
    token = create_access_token(owner)
    try:
        with TestClient(app) as client:
            headers = {'Authorization': 'Bearer ' + token}
            health = client.get('/health')
            assert health.status_code == 200 and health.json()['version'] == '1.0.0-build14'
            status = client.get('/onboarding/status', headers=headers)
            assert status.status_code == 200
            assert 'catalogue_discovery_v1' in status.json()['recommendation_features']
            assert client.get('/catalog/discovery').status_code == 401
            for category in ('shoes', 'outerwear'):
                page = client.get('/catalog/discovery', params={'category': category, 'limit': 2}, headers=headers)
                assert page.status_code == 200
                body = page.json()
                assert body['personalized'] is False and body['size_verification'] == 'not_verified'
                assert body['products'], 'real_http_category_missing'
                for row in body['products']:
                    assert row['catalogue_discovery'] is True and row['size_verification'] == 'not_verified'
                    assert row['size_notice'] == 'Размер нужно уточнить у магазина'
                product_id = body['products'][0]['id']
                detail = client.get('/catalog/discovery/' + product_id, headers=headers)
                assert detail.status_code == 200 and detail.json()['catalogue_discovery'] is True
                assert client.get('/catalog/discovery/' + product_id).status_code == 401
                checks.append({'category': category, 'rows': len(body['products']),
                               'list_status': 200, 'detail_status': 200, 'unauthenticated_status': 401})
            # Browsing does not let an unconfirmed cold account into the strict feed.
            feed = client.get('/feed', params={'limit': 2}, headers=headers)
            assert feed.status_code == 409
            with SessionLocal() as db:
                assert db.scalar(select(func.count()).select_from(TasteProfile)) == before['taste_profiles']
                assert db.scalar(select(func.count()).select_from(RecommendationEventV2)) == before['recommendation_events_v2']
    finally:
        with engine.begin() as connection:
            for table in reversed(Base.metadata.sorted_tables):
                if 'user_id' in table.c:
                    connection.execute(delete(table).where(table.c.user_id == owner))
            connection.execute(delete(User).where(User.id == owner))
    with SessionLocal() as db:
        after = {model.__tablename__: db.scalar(select(func.count()).select_from(model))
                 for model in (User, TasteProfile, RecommendationEventV2)}
        assert after == before
    print(json.dumps({'status': 'passed', 'checks': checks, 'health_version': '1.0.0-build14',
                      'capability_advertised': True, 'cold_strict_feed_status': 409,
                      'fixture_cleanup': 'committed', 'external_provider_calls': 0}))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'status': 'failed', 'error_type': type(error).__name__, 'raw_error_omitted': True}))
        raise SystemExit(1) from None
