"""Measure actual isolated-catalogue activation; every new user is rolled back.

The catalogue is real, while the profile and 10-like/5-dislike reactions are
controlled test inputs. No completed_at, taste, size or stock evidence is faked.
One UTC clock is supplied by the host for both immutable images. The helper
prints aggregate counts, hashes and CPU/SQL timings, never product URLs or env.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
import cProfile
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import pstats
import sys
from time import perf_counter
from uuid import uuid4


FOCUSED_FUNCTIONS = frozenset({
    'candidate_pool', 'swipe_deck', 'personalized_feed', 'complete_wow',
    'product_ok', 'evaluate_eligibility', 'requested_size_for_product',
    'size_profile_to_api', 'exact_size_match', 'resolve_product_sizes',
    'resolve_size', 'normalize_category', 'product_role', 'evaluate',
    '_base_affinity', 'reliable_taste_features', '_build_product_dna',
    '_json_value', 'normalize_text', '_lexical_matches', 'blocked_evidence',
    'matched_values', 'product_taste_features', 'compute_taste_score',
})


def ids_hash(ids):
    return hashlib.sha256(json.dumps(list(ids), separators=(',', ':')).encode()).hexdigest()


@contextmanager
def fixed_application_clock(as_of):
    """Freeze application read gates, without modifying source observations.

    Modules that are imported by feedback/completion are loaded first so their
    own datetime imports use the same clock. SQLAlchemy model defaults retain
    their real-clock callables; they create only rolled-back test-user rows.
    """
    clock_module_names = (
        'app.services.activation_service', 'app.services.activation_policy',
        'app.services.catalog.stock_freshness', 'app.services.product_eligibility',
        'app.services.mie_scoring', 'app.services.user_twin_service',
        'app.services.product_event_service', 'app.services.outfit_service',
        'app.services.outfit_engine_v2', 'app.services.candidate_retrieval_service',
        'app.services.recommendation_engine', 'app.services.behavior_profile',
        'app.api.outfits', 'app.schemas.api_product', 'app.services.personalization_scoring',
    )
    for name in clock_module_names:
        importlib.import_module(name)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return as_of.astimezone(tz) if tz is not None else as_of.replace(tzinfo=None)

        @classmethod
        def utcnow(cls):
            return as_of.astimezone(timezone.utc).replace(tzinfo=None)

        @classmethod
        def today(cls):
            return cls.now()

    patched = []
    for name in clock_module_names:
        module = sys.modules[name]
        if getattr(module, 'datetime', None) is datetime:
            patched.append(module)
            module.datetime = FrozenDateTime
    try:
        yield [module.__name__ for module in patched]
    finally:
        for module in patched:
            module.datetime = datetime


def profile_rows(profiler, top):
    stats = pstats.Stats(profiler)
    ranked = sorted(stats.stats.items(), key=lambda pair: pair[1][3], reverse=True)

    def row(key, values):
        filename, line, function = key
        primitive, total, own, cumulative, _callers = values
        filename = filename.replace('\\', '/')
        if filename.startswith('/app/'):
            filename = filename.removeprefix('/app/')
        else:
            filename = Path(filename).name
        return {'file': filename, 'line': line, 'function': function,
                'primitive_calls': primitive, 'total_calls': total,
                'own_seconds': round(own, 6), 'cumulative_seconds': round(cumulative, 6)}

    return {'cumulative_top': [row(key, values) for key, values in ranked[:top]],
            'focused_calls': [row(key, values) for key, values in ranked
                              if key[2] in FOCUSED_FUNCTIONS]}


def measured_phase(engine, operation, *, profile=False, top=18):
    from sqlalchemy import event
    statements = Counter()
    sql_seconds = []

    def before(_conn, _cursor, statement, _params, context, _many):
        context.activation_audit_started_at = perf_counter()
        statements[statement.split()[0].upper()] += 1

    def after(_conn, _cursor, _statement, _params, context, _many):
        sql_seconds.append(perf_counter() - context.activation_audit_started_at)

    profiler = cProfile.Profile() if profile else None
    event.listen(engine, 'before_cursor_execute', before)
    event.listen(engine, 'after_cursor_execute', after)
    started = perf_counter()
    try:
        result = profiler.runcall(operation) if profiler else operation()
    finally:
        elapsed = perf_counter() - started
        event.remove(engine, 'before_cursor_execute', before)
        event.remove(engine, 'after_cursor_execute', after)
    measurement = {'seconds': round(elapsed, 6), 'profiled': profile,
                   'sql_execute_seconds': round(sum(sql_seconds), 6),
                   'statements': dict(statements)}
    if profiler:
        measurement.update(profile_rows(profiler, top))
    return result, measurement


def check_wow_items(db, wow, fit):
    from app.models import Product
    from app.services.product_eligibility import evaluate_eligibility
    from app.services.garment_roles import product_role
    from app.services.outfit_quality import outfit_compatible
    returned = checked = 0
    for outfit in wow['outfits']:
        items, snapshots = outfit['items'], outfit['products']
        assert set(items) in ({'top', 'bottom', 'shoes'}, {'one_piece', 'shoes'})
        assert set(items) == set(snapshots)
        products = []
        for slot, pid in items.items():
            returned += 1
            product = db.get(Product, pid)
            assert product is not None and snapshots[slot]['id'] == pid
            assert product_role(product) == slot
            assert evaluate_eligibility(product, fit).eligible
            checked += 1
            products.append(product)
        assert sum(product.price for product in products) <= fit.budget_max
        assert outfit_compatible(products)
    return returned, checked


def run_once(*, index, profile, top):
    from sqlalchemy import event, func, select, text
    from app.db import SessionLocal, engine
    from app.models import ActivationSession, FitProfile, Product, User
    from app.services import activation_service as activation
    from app.services.product_eligibility import evaluate_eligibility

    row = {'index': index, 'profiled': profile, 'activation_completed': False,
           'test_writes_rolled_back': False, 'phases': {}}
    with SessionLocal() as db:
        db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ'))
        product_count = db.scalar(select(func.count()).select_from(Product))
        uid = str(uuid4())
        user = User(id=uid, email='activation-audit-' + uuid4().hex + '@example.test',
                    password_hash='disabled-test-account')

        def no_commit():
            raise AssertionError('Benchmark transactions must never commit')

        def no_catalogue_writes(session, _context, _instances):
            assert not any(isinstance(obj, Product) for collection in
                           (session.new, session.dirty, session.deleted) for obj in collection), \
                'Benchmark must not change catalogue rows'

        db.commit = no_commit
        event.listen(db, 'before_flush', no_catalogue_writes)
        try:
            db.add(user)
            db.flush()
            session, row['phases']['configure'] = measured_phase(engine, lambda: activation.configure(
                db, user, gender_target='womenswear', age_group='25-34', height=168, clothing_size='L',
                clothing_size_system='LETTER', shoe_size='38', shoe_size_system='EU',
                styles=['casual', 'minimalism'], price_segment='mid'), profile=profile, top=top)
            deck, row['phases']['cold_deck'] = measured_phase(
                engine, lambda: activation.swipe_deck(db, user, session), profile=profile, top=top)
            db.flush()  # Match API request boundaries without committing test data.
            deck_ids = [product['id'] for product in deck['products']]
            row.update(bootstrap_cards=len(deck_ids), bootstrap_ids_sha256=ids_hash(deck_ids))
            fit = db.scalar(select(FitProfile).where(FitProfile.user_id == uid))
            assert all(evaluate_eligibility(db.get(Product, pid), fit).eligible for pid in deck_ids)
            row.update(eligible_deck_cards_checked=len(deck_ids),
                       eligibility_basis='exact_confirmed_size_for_sold_garment_role_and_fresh_stock')
            if len(deck_ids) < activation.MIN_SWIPES:
                row['reason'] = 'not_enough_eligible_bootstrap_cards'
            else:
                assert activation.MIN_SWIPES == 15, 'This harness models the real 15-rating boundary'

                def style_swipe():
                    for position, pid in enumerate(deck_ids[:15]):
                        activation.swipe_feedback(db, user, session, pid,
                                                  'like' if position < 10 else 'dislike')
                        db.flush()

                _, row['phases']['style_swipe_15'] = measured_phase(
                    engine, style_swipe, profile=profile, top=top)
                assert activation.rated_count(session) == 15 and activation.can_complete(session)
                wow, row['phases']['complete_wow'] = measured_phase(
                    engine, lambda: activation.complete_wow(db, user, session), profile=profile, top=top)
                db.flush()
                assert wow['completed'] and session.completed_at is not None
                returned_wow, checked_wow = check_wow_items(db, wow, fit)
                feed, row['phases']['first_feed'] = measured_phase(
                    engine, lambda: activation.personalized_feed(db, user, session, limit=20),
                    profile=profile, top=top)
                assert all(evaluate_eligibility(item.product, fit).eligible for item in feed)
                feed_ids = [item.product.id for item in feed]
                row.update(activation_completed=True, style_swipe_rated=activation.rated_count(session),
                           wow_outfits=len(wow['outfits']), wow_items_returned=returned_wow,
                           eligible_wow_items_checked=checked_wow, feed_cards=len(feed),
                           eligible_feed_cards_checked=len(feed),
                           feed_ids_sha256=ids_hash(feed_ids),
                           learned_taste_from_real_swipes=True)
            row['catalogue_product_count'] = product_count
        finally:
            event.remove(db, 'before_flush', no_catalogue_writes)
            db.rollback()
        # These are fresh reads after rollback, not the former identity map.
        assert db.get(User, uid) is None and db.get(ActivationSession, uid) is None
        assert db.scalar(select(func.count()).select_from(Product)) == product_count
        db.rollback()
        row['test_writes_rolled_back'] = True
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs', type=int, default=3)
    parser.add_argument('--profile', action='store_true',
                        help='Profile one additional run; exclude its timings from medians.')
    parser.add_argument('--top', type=int, default=18)
    args = parser.parse_args()
    if not 1 <= args.runs <= 5 or not 1 <= args.top <= 50:
        parser.error('Use 1..5 measured runs and 1..50 profiler rows.')
    sys.path.insert(0, '/app')
    from sqlalchemy.engine import make_url
    url = make_url(os.environ['DATABASE_URL'])
    assert url.host == 'modish_foundation_test_pg'
    assert url.database == url.username == 'modish_test'
    assert os.environ.get('PYTHON_DOTENV_DISABLED') == '1'
    as_of = datetime.fromisoformat(os.environ['AUDIT_FIXED_NOW_UTC'])
    assert as_of.tzinfo is not None and as_of.utcoffset().total_seconds() == 0
    clock_basis = os.environ.get('AUDIT_CLOCK_BASIS', 'current_wall_clock')
    assert clock_basis in {'current_wall_clock', 'explicit_historical_replay'}
    report = {'scope': 'isolated_real_catalogue_controlled_profile',
              'image_id': os.environ['AUDIT_IMAGE_ID'],
              'archive_sha256': os.environ['AUDIT_ARCHIVE_SHA256'],
              'fixed_now_utc': as_of.isoformat(), 'clock_is_fixed_for_read_gates': True,
              'clock_basis': clock_basis, 'historical_replay': clock_basis == 'explicit_historical_replay',
              'catalogue_is_synthetic': False, 'profile_and_reactions_are_controlled': True,
              'profile': {'clothing_size_system': 'LETTER', 'clothing_size': 'L',
                          'shoe_size_system': 'EU', 'shoe_size': '38', 'height_cm': 168,
                          'styles': ['casual', 'minimalism'], 'price_segment': 'mid'},
              'reaction_protocol': {'real_style_swipes': 15, 'like': 10, 'dislike': 5},
              'stock_timestamps_modified': False, 'runs': [], 'profile_run': None,
              'limitation': 'One controlled profile does not establish catalogue coverage, '
                            'human taste quality or production request latency. CPU quota and '
                            'SQL/ORM costs apply; SQL timing excludes fetch and object creation.'}
    with fixed_application_clock(as_of) as clock_modules:
        report['clock_modules'] = clock_modules
        for index in range(1, args.runs + 1):
            report['runs'].append(run_once(index=index, profile=False, top=args.top))
            print('activation_benchmark_run_completed ' + str(index), file=sys.stderr, flush=True)
        if args.profile:
            report['profile_run'] = run_once(index=args.runs + 1, profile=True, top=args.top)
            print('activation_benchmark_profile_completed', file=sys.stderr, flush=True)
    all_rows = report['runs'] + ([report['profile_run']] if report['profile_run'] else [])
    assert all(row['test_writes_rolled_back'] for row in all_rows)
    report['test_writes_rolled_back'] = True
    report['completed_runs'] = sum(row['activation_completed'] for row in report['runs'])
    report['medians_seconds'] = {}
    for phase in ('configure', 'cold_deck', 'style_swipe_15', 'complete_wow', 'first_feed'):
        values = sorted(row['phases'][phase]['seconds'] for row in report['runs'] if phase in row['phases'])
        if values:
            middle = len(values) // 2
            report['medians_seconds'][phase] = (values[middle] if len(values) % 2 else
                                                (values[middle - 1] + values[middle]) / 2)
    report['measurement_status'] = ('completed_real_activation' if report['completed_runs'] == args.runs
                                    else 'insufficient_real_catalogue_bootstrap')
    print(json.dumps(report, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
