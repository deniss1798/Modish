"""Offline activation CPU/query profile using a deterministic synthetic catalog.

Uses only an in-memory SQLite database, disables dotenv and outbound sockets,
and accepts no external data, endpoint, credential or database URL.
Run with backend/.venv/Scripts/python.exe scripts/profile_activation_v20.py.
The synthetic timings are diagnostic and are not production latency estimates.
"""
from __future__ import annotations

import argparse
from collections import Counter
import cProfile
import hashlib
import json
from pathlib import Path
import pstats
from time import perf_counter

from benchmark_activation_feedback import AS_OF, isolated_runtime


CATEGORIES = ('футболки', 'рубашки', 'джемперы', 'джинсы', 'брюки', 'юбки', 'платья', 'обувь', 'верхний_слой')
TITLES = ('Футболка хлопковая', 'Хлопковая рубашка', 'Джемпер из хлопка', 'Джинсы прямые',
          'Брюки прямого кроя', 'Юбка миди', 'Платье миди', 'Лоферы кожаные', 'Куртка демисезонная')
FOCUSED_NAMES = frozenset({'candidate_pool', 'product_ok', 'evaluate_eligibility',
    'requested_size_for_product', 'size_profile_to_api', 'exact_size_match', 'resolve_product_sizes',
    'resolve_size', 'normalize_category', 'product_role', 'is_sports_item', 'evaluate', '_base_affinity',
    'reliable_taste_features', '_build_product_dna', '_json_value', 'normalize_text', '_lexical_matches',
    'blocked_evidence', 'matched_values', 'product_taste_features', 'compute_taste_score'})


def fixture_rows(count: int, *, eligible_every: int):
    for i in range(count):
        category_index = i % len(CATEGORIES)
        pid = f'{i:036d}'
        shoe = category_index == 7
        eligible = (i // len(CATEGORIES)) % eligible_every == 0
        raw = {'size_system': 'EU' if shoe else 'LETTER', 'material': 'cotton',
            'fit': 'regular', 'silhouette': 'straight', 'pattern': 'solid',
            'length': 'midi', 'sleeve': 'long', 'texture': 'ribbed', 'season': 'demiseason',
            'occasion': 'work', 'formality': 'smart', 'ignored_tracking_attribute': 'synthetic'}
        yield dict(id=pid, external_id=pid, group_id=pid, source='baon',
            brand=f'Brand {i % 5}', title=TITLES[category_index],
            category='Женская одежда / ' + ('куртки' if category_index == 8 else CATEGORIES[category_index]),
            price=1500 + i % 3000, currency='RUB', gender_target='womenswear',
            available_sizes=['38' if eligible else '39'] if shoe else ['M' if eligible else 'L'],
            feed_raw_json=raw, raw_params_json={'Материал': 'cotton', 'Посадка': 'regular'},
            colors=['black'], style_tags=['casual', 'minimalism'],
            image_url=f'https://catalog.example.test/{pid}.jpg',
            product_url=f'https://catalog.example.test/product/{pid}',
            is_active=1, is_available=1, availability_status='in_stock', is_deleted_from_feed=0,
            last_seen_in_feed_at=AS_OF)


def profile_phase(engine, operation, *, name: str, top: int):
    from sqlalchemy import event
    statements = Counter()
    sql_seconds = []
    def before(_conn, _cursor, statement, _params, context, _many):
        context.activation_profile_started_at = perf_counter()
        statements[statement.split()[0].upper()] += 1
    def after(_conn, _cursor, _statement, _params, context, _many):
        sql_seconds.append(perf_counter() - context.activation_profile_started_at)
    event.listen(engine, 'before_cursor_execute', before)
    event.listen(engine, 'after_cursor_execute', after)
    profiler = cProfile.Profile()
    started = perf_counter()
    try:
        result = profiler.runcall(operation)
    finally:
        elapsed = perf_counter() - started
        event.remove(engine, 'before_cursor_execute', before)
        event.remove(engine, 'after_cursor_execute', after)
    stats = pstats.Stats(profiler)
    def row(key, values):
        primitive, total, own, cumulative, _callers = values
        filename, line, function = key
        try:
            filename = str(Path(filename).resolve().relative_to(Path(__file__).resolve().parents[1]))
        except ValueError:
            filename = Path(filename).name
        return {'file': filename, 'line': line, 'function': function,
            'primitive_calls': primitive, 'total_calls': total,
            'own_seconds': round(own, 6), 'cumulative_seconds': round(cumulative, 6)}
    ranked = sorted(stats.stats.items(), key=lambda pair: pair[1][3], reverse=True)
    ids = [product['id'] for product in result['products']] if isinstance(result, dict) else [r.product.id for r in result]
    return {'phase': name, 'seconds_with_profiler': round(elapsed, 6),
        'sql_execute_seconds': round(sum(sql_seconds), 6), 'statements': dict(statements),
        'returned': len(ids), 'ids_sha256': hashlib.sha256(json.dumps(ids).encode()).hexdigest(),
        'cumulative_top': [row(key, values) for key, values in ranked[:top]],
        'focused_calls': [row(key, values) for key, values in ranked if key[2] in FOCUSED_NAMES]}


def run_profile(count: int, *, eligible_every: int, top: int):
    with isolated_runtime():
        from sqlalchemy import create_engine, insert
        from sqlalchemy.orm import Session
        from app.db import Base
        from app.models import Product, User, UserTasteFeature
        from app.services import activation_service as activation
        from app.services.user_twin_service import DNA_TASTE_PREFIX
        engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(engine)
        try:
            with Session(engine, autoflush=False) as db:
                user = User(id='offline-activation-profiler', email='profile@example.test', password_hash='unused')
                db.add(user); db.flush()
                session = activation.configure(db, user, age_group='25-34', height=170,
                    clothing_size='M', clothing_size_system='LETTER', shoe_size='38', shoe_size_system='EU',
                    styles=['casual', 'minimalism'], price_segment='mass')
                profile, fit, taste = activation._preferences(db, user)
                fit.interest_categories = list(CATEGORIES)
                rows = fixture_rows(count, eligible_every=eligible_every)
                while batch := [row for _, row in zip(range(1000), rows)]:
                    db.execute(insert(Product), batch)
                db.flush()
                phases = [profile_phase(engine, lambda: activation.swipe_deck(db, user, session),
                    name='cold_deck', top=top)]
                session.completed_at = AS_OF
                db.add(UserTasteFeature(id='offline-cotton-preference', user_id=user.id,
                    feature_type='material', feature_value=DNA_TASTE_PREFIX + 'cotton',
                    preference_score=.9, confidence=.9, positive_count=10, negative_count=0,
                    last_signal_at=AS_OF, last_decay_at=AS_OF, created_at=AS_OF, updated_at=AS_OF))
                db.flush()
                phases.append(profile_phase(engine, lambda: activation.personalized_feed(db, user, session),
                    name='semantic_first_feed', top=top))
                phases.append(profile_phase(engine,
                    lambda: activation.personalized_feed(db, user, session, scenario='office'),
                    name='semantic_office_feed', top=top))
                return {'synthetic': True, 'database': 'sqlite_memory', 'external_calls': 'forbidden',
                    'catalogue_products': count, 'eligible_every': eligible_every,
                    'note': 'cProfile overhead included; SQL execute excludes result-fetch/ORM costs. '
                        'Synthetic fixture cannot predict production PostgreSQL or CPU-quota latency.',
                    'phases': phases}
        finally:
            engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--products', type=int, default=3000)
    parser.add_argument('--eligible-every', type=int, default=1,
        help='Only one in this many products per category has the requested exact size.')
    parser.add_argument('--top', type=int, default=18)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.products < 1 or args.eligible_every < 1 or args.top < 1:
        parser.error('Numeric options must be positive.')
    report = run_profile(args.products, eligible_every=args.eligible_every, top=args.top)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
