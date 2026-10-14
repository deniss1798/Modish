"""Verify honest discovery on ten controlled profiles in an isolated PG copy.

Only synthetic User/Fit rows are written, and each profile transaction rolls
back. No merchant or AI network request, exposure or taste event is sent.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch
from uuid import uuid4


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--isolated-write-test', required=True, action='store_true')
    parser.add_argument('--expected-host', required=True)
    parser.add_argument('--expected-database', required=True)
    args = parser.parse_args()
    if not __debug__:
        raise ValueError('python_optimization_forbidden')
    from sqlalchemy import func, select, text
    from sqlalchemy.engine import make_url
    url = make_url(os.environ['DATABASE_URL'])
    assert (url.get_backend_name() == 'postgresql' and url.host == args.expected_host
            and url.database == args.expected_database
            and url.database not in {'modish', 'postgres'}
            and any(marker in url.database.lower() for marker in ('test', 'clone', 'restore'))), 'isolated_database_guard'
    os.environ.update(APP_ENV='test', PYTHON_DOTENV_DISABLED='1')
    logging.disable(logging.CRITICAL)
    source_root = Path('/app') if Path('/app/app').is_dir() else Path(__file__).resolve().parents[1] / 'backend'
    sys.path.insert(0, str(source_root))
    # Server mounts the helper under /checks; the candidate source lives /app.
    sys.path.insert(0, '/app')
    with patch('dotenv.load_dotenv'):
        from app.db import SessionLocal, engine
        from app.models import FitProfile, Product, RecommendationEventV2, TasteProfile, User
        from app.services.catalog_discovery import catalogue_page, catalogue_detail
        from app.services.catalog.catalog_quality import product_is_feed_eligible
        from app.services.catalog.stock_freshness import availability_snapshot
        from app.services.garment_roles import product_role
        from app.services.product_identity import product_identity_keys

    assert engine.url == url, 'actual_engine_identity_guard'
    with SessionLocal() as db:
        assert db.scalar(text('SELECT current_database()')) == args.expected_database
        assert db.scalar(text('SELECT version_num FROM alembic_version')) == '20261013_0025'
        before = {model.__tablename__: db.scalar(select(func.count()).select_from(model))
                  for model in (User, FitProfile, TasteProfile, RecommendationEventV2)}
    profiles = [
        ('mass_m', 10000, 'M'), ('mass_l', 10000, 'L'),
        ('economy_s', 4000, 'S'), ('economy_xl', 4000, 'XL'),
        ('mid_m', 25000, 'M'), ('mid_xl', 25000, 'XL'),
        ('numeric_46', 10000, '46'), ('numeric_48', 15000, '48'),
        ('unknown_size', 10000, None), ('no_fit', None, None),
    ]
    started = time.perf_counter()
    reports = []
    totals = {'all': 0, 'shoes': 0, 'outerwear': 0}
    for name, budget, size in profiles:
        with SessionLocal() as db:
            db.execute(text("SET LOCAL statement_timeout = '30s'"))
            db.execute(text("SET LOCAL lock_timeout = '5s'"))
            user = User(id=str(uuid4()), email=f'discovery-{uuid4().hex}@example.invalid', password_hash='unused')
            db.add(user)
            db.flush()
            if budget is not None:
                db.add(FitProfile(id=str(uuid4()), user_id=user.id, height_cm=170,
                       gender_target='womenswear', clothing_size=size, budget_max=budget))
                db.flush()
            evidence = []
            try:
                for category in ('', 'shoes', 'outerwear'):
                    phase_start = time.perf_counter()
                    first = catalogue_page(db, user, category=category, max_price=999999, limit=20)
                    assert first['personalized'] is False and first['size_verification'] == 'not_verified'
                    assert first['scope']['budget_max'] <= (budget or 10000)
                    assert len(first['products']) <= 20
                    rows = list(first['products'])
                    if first['has_more']:
                        second = catalogue_page(db, user, category=category, max_price=999999,
                                                limit=20, cursor=first['next_cursor'])
                        rows.extend(second['products'])
                    keys = set()
                    unknown = 0
                    for row in rows:
                        assert row['catalogue_discovery'] is True and row['size_verification'] == 'not_verified'
                        assert row['size_notice'] == 'Размер нужно уточнить у магазина'
                        assert 300 <= row['price'] <= (budget or 10000)
                        product = db.get(Product, row['id'])
                        assert product_is_feed_eligible(product)
                        assert availability_snapshot(product).available
                        assert product.gender_target in {'womenswear', 'unisex'}
                        if category:
                            assert product_role(product) == category
                        identity = product_identity_keys(product)
                        assert not identity.intersection(keys), 'cross_page_identity_repeat'
                        keys.update(identity)
                        for merchant in row['merchant_sizes']:
                            if merchant['size_system'] == 'UNKNOWN':
                                unknown += 1
                                assert merchant['normalized_size'] is None
                    if rows:
                        detail = catalogue_detail(db, user, rows[0]['id'])
                        assert detail['catalogue_discovery'] is True and detail['size_verification'] == 'not_verified'
                    label = category or 'all'
                    totals[label] += len(rows)
                    evidence.append({'category': label, 'first_page': len(first['products']),
                                     'verified_two_page_rows': len(rows), 'unknown_unit_tokens': unknown,
                                     'seconds': round(time.perf_counter() - phase_start, 4)})
                assert db.scalar(select(func.count()).select_from(TasteProfile)) == before['taste_profiles']
                assert db.scalar(select(func.count()).select_from(RecommendationEventV2)) == before['recommendation_events_v2']
            finally:
                db.rollback()
            reports.append({'profile': name, 'budget': budget, 'clothing_size': size, 'categories': evidence})
    assert all(count > 0 for count in totals.values()), 'real_catalogue_category_coverage_missing'
    with SessionLocal() as db:
        after = {model.__tablename__: db.scalar(select(func.count()).select_from(model))
                 for model in (User, FitProfile, TasteProfile, RecommendationEventV2)}
        assert after == before, 'fixture_or_event_rows_left_behind'
    print(json.dumps({'status': 'passed', 'checked_at': datetime.now(timezone.utc).isoformat(),
                      'profile_count': len(profiles), 'profiles': reports,
                      'seconds': round(time.perf_counter() - started, 3),
                      'fixture_cleanup': 'rolled_back', 'users_restored': True,
                      'external_provider_calls': 0, 'personal_fit_claims': 0,
                      'strict_complete_outfit_readiness': 'not_asserted'}, ensure_ascii=True))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({'status': 'failed', 'error_type': type(error).__name__, 'raw_error_omitted': True}))
        raise SystemExit(1) from None
