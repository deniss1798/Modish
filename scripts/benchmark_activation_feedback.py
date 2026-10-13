"""Offline technical replay of activation feedback, never a business-lift estimate.

Run with the backend Python: python scripts/benchmark_activation_feedback.py
No database URL, input catalogue, network endpoint or credential is accepted.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CHECKPOINTS = (0, 10, 30, 100)
AS_OF = datetime(2026, 9, 24, 12, tzinfo=timezone.utc)
CATEGORIES = ('рубашки', 'брюки', 'юбки', 'платья', 'футболки', 'обувь')
STYLES = ('casual', 'romantic')
TITLES = ('Рубашка однотонная', 'Брюки прямые', 'Юбка миди',
          'Платье миди', 'Футболка базовая', 'Лоферы кожаные')


class FrozenClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return AS_OF.astimezone(tz) if tz else AS_OF.replace(tzinfo=None)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _deny_network(*args, **kwargs):
    raise RuntimeError('The activation benchmark is offline; network access is forbidden.')


@contextmanager
def isolated_runtime():
    """Disable dotenv before app imports and reject every outbound socket."""
    environment = {'DATABASE_URL': 'sqlite:///:memory:', 'APP_ENV': 'test',
                   'JWT_SECRET': 'offline-benchmark-not-a-real-secret', 'PYTHON_DOTENV_DISABLED': '1'}
    for name in ('SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'PATH'):
        if name in os.environ:
            environment[name] = os.environ[name]
    backend = str(ROOT / 'backend')
    if backend not in sys.path:
        sys.path.insert(0, backend)
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, environment, clear=True))
        stack.enter_context(patch('dotenv.load_dotenv'))
        for target in ('socket.create_connection', 'socket.socket.connect', 'socket.socket.connect_ex'):
            stack.enter_context(patch(target, _deny_network))
        # Only clocks are fixed. Event handling, weights, hard gates and ranking
        # are the application implementation, not stubbed benchmark versions.
        for module in ('app.services.activation_service', 'app.api.recommendations', 'app.services.product_event_service',
                       'app.services.catalog.stock_freshness', 'app.services.user_twin_service',
                       'app.services.mie_scoring',
                       'app.services.recommendation_engine', 'app.services.onboarding_service',
                       'app.services.outfit_engine_v2'):
            stack.enter_context(patch(f'{module}.datetime', FrozenClock))
        yield


def fixture_rows():
    """100 training items and a disjoint, balanced, fixed 120-item holdout."""
    rows = []
    for split, count in [('training', 100), ('holdout', 120)]:
        for i in range(count):
            category_index = (i // 2) % len(CATEGORIES)
            style = STYLES[i % 2]
            pid = digest([split, i])[:36]
            shoes = category_index == 5
            rows.append(dict(id=pid, external_id=pid, group_id=pid, source='befree',
                brand=f'{style}-{(i // 12) % 3}', title=TITLES[category_index],
                category=CATEGORIES[category_index], price=1500, currency='RUB',
                gender_target='womenswear', available_sizes=['38'] if shoes else ['M'],
                feed_raw_json={'size_system': 'EU' if shoes else 'LETTER'},
                colors=['white' if style == 'casual' else 'burgundy'], style_tags=[style],
                image_url=f'https://catalog.example.test/images/{pid}.jpg',
                product_url=f'https://catalog.example.test/product/{pid}',
                is_active=1, is_available=1, availability_status='in_stock', is_deleted_from_feed=0,
                last_seen_in_feed_at=AS_OF, split=split))
    # Ineligible examples are real inputs to the hard filter, not excluded by
    # fixture labels. This prevents a trivial 100% validity report.
    invalid = {
        'wrong_gender': {'gender_target': 'menswear'},
        'over_budget': {'price': 12000},
        'unknown_size': {'available_sizes': []},
        'wrong_size': {'available_sizes': ['L']},
        'stale_stock': {'last_seen_in_feed_at': AS_OF - timedelta(hours=73)},
        'unknown_stock': {'last_seen_in_feed_at': None},
        'future_stock': {'last_seen_in_feed_at': AS_OF + timedelta(hours=1)},
        'unavailable': {'is_available': 0},
        'missing_image': {'image_url': ''},
        'missing_link': {'product_url': ''},
        'childrenswear': {'title': 'Рубашка для девочки'},
        'non_fashion': {'title': 'Крем для лица'},
    }
    for label, changes in invalid.items():
        row = {**rows[0], 'id': f'invalid-{label}', 'external_id': label,
               'group_id': label, 'split': 'invalid', **changes}
        rows.append(row)
    return rows


def ranking_metrics(products, *, desired_style, valid_ids, disliked_ids):
    ids = [p.id for p in products]
    n = len(ids)
    return {'returned': n, 'unique': len(set(ids)), 'empty': n == 0,
            'hard_filter_pass_rate': sum(pid in valid_ids for pid in ids) / n if n else None,
            'heldout_style_relevance': sum(desired_style in p.style_tags for p in products) / n if n else None,
            'category_count': len({p.category for p in products}),
            'brand_count': len({p.brand for p in products}),
            'disliked_returned': len(set(ids) & disliked_ids), 'ids': ids}


def top_change(before, after):
    """Membership and order are separate; empty comparisons have no rate."""
    left, right = [p.id for p in before], [p.id for p in after]
    return {'removed_ids': sorted(set(left) - set(right)),
            'added_ids': sorted(set(right) - set(left)),
            'position_change_rate': sum(a != b for a, b in zip(left, right)) / len(left)
                if left and len(left) == len(right) else None}


def replay_checkpoint(desired_style, event_count):
    """Every checkpoint starts from an empty SQLite database, with prefix only."""
    if desired_style not in STYLES or event_count not in CHECKPOINTS:
        raise ValueError('Unsupported fixed benchmark checkpoint or persona.')
    from fastapi import HTTPException
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session
    from app.db import Base
    from app.models import User, Product, RecommendationEventV2
    from app.services import activation_service as a
    from app.api.recommendations import EventRequest, record_product_event

    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    try:
        with Session(engine, autoflush=False) as db:
            user = User(id=f'benchmark-{desired_style}-{event_count}',
                        email=f'{desired_style}-{event_count}@example.test', password_hash='unused')
            db.add(user)
            db.flush()
            activation = a.configure(db, user, age_group='25-34', height=170, clothing_size='M', shoe_size='38',
                styles=list(STYLES), price_segment='mass', clothing_size_system='LETTER', shoe_size_system='EU')
            profile, fit, taste = a._preferences(db, user)
            # Explicit fixed interests prevent evolving category retrieval from
            # being confused with the measured effect of ranking weights.
            fit.interest_categories = list(CATEGORIES)
            by_split = {'training': [], 'holdout': [], 'invalid': []}
            for raw in fixture_rows():
                fields = dict(raw)
                split = fields.pop('split')
                product = Product(**fields)
                db.add(product)
                by_split[split].append(product)
            db.flush()
            training = by_split['training']
            holdout = by_split['holdout']
            train_ids = {p.id for p in training}
            holdout_ids = {p.id for p in holdout}
            assert not train_ids & holdout_ids
            all_products = [p for products in by_split.values() for p in products]
            eligible = [p for p in all_products if a.product_ok(p, fit, activation)]
            valid_ids = {p.id for p in eligible}
            assert valid_ids == train_ids | holdout_ids, 'Hard eligibility fixture failed.'
            # A fixture-issued deck makes the sequence stable across algorithms.
            # This benchmark isolates ranking; it does not evaluate deck retrieval.
            activation.swipe_product_ids = [p.id for p in training[:a.MIN_SWIPES]]
            prefix = []
            for i, product in enumerate(training[:event_count]):
                reaction = 'like' if desired_style in product.style_tags else 'dislike'
                prefix.append((product.id, reaction))
                if i < a.MIN_SWIPES:
                    a.swipe_feedback(db, user, activation, product.id, reaction)
                    if i + 1 == a.MIN_SWIPES:
                        a.complete_wow(db, user, activation)
                else:
                    record_product_event(db, user, EventRequest(product_id=product.id,
                        event_type=reaction, meta={'surface': 'feed'}), commit=False)
            db.flush()
            try:
                a.require_warm_feed(db, user)
                blocked = False
            except HTTPException as exc:
                assert exc.status_code == 409
                blocked = True
            events = list(db.scalars(select(RecommendationEventV2).where(
                RecommendationEventV2.user_id == user.id)))
            observed_ids = {e.product_id for e in events}
            assert len(events) == event_count and observed_ids == {pid for pid, _ in prefix}
            assert not observed_ids & holdout_ids, 'Heldout labels leaked into feedback.'
            assert blocked == (event_count < a.MIN_SWIPES), 'Activation gate violated.'
            disliked = {pid for pid, reaction in prefix if reaction == 'dislike'}
            zero_taste = SimpleNamespace(style_weights={}, category_weights={}, color_weights={}, brand_weights={})

            from app.services.mie_scoring import load_user_taste_feature_map
            twin = load_user_taste_feature_map(db, user_id=user.id, as_of=AS_OF)
            def ranked(pool, weights):
                learned_twin = twin if weights is taste else None
                ordered = sorted(pool, key=lambda p: (-a._affinity(p, profile, weights, taste_features=learned_twin), p.id))
                return a.diverse_products(ordered, 20, exploration=True)

            after_exclusions = [p for p in eligible if p.id not in observed_ids]
            control_before = ranked(eligible, zero_taste)
            control_after = ranked(after_exclusions, zero_taste)
            learned_after = ranked(after_exclusions, taste)
            control_holdout = ranked(holdout, zero_taste)
            learned_holdout = ranked(holdout, taste)
            # Rank unchanged heldout candidates separately from exclusions.
            metrics = lambda products: ranking_metrics(products, desired_style=desired_style,
                                                       valid_ids=valid_ids, disliked_ids=disliked)
            return {'persona': desired_style, 'events': event_count, 'recorded_events': len(events),
                'likes': sum(r == 'like' for _, r in prefix), 'dislikes': len(disliked),
                'prefix_sha256': digest(prefix), 'cold_feed_blocked': blocked,
                'metric_surface': 'offline_rank_probe_only' if blocked else 'offline_warm_rank_probe',
                'catalog_count': len(all_products), 'hard_eligible_count': len(eligible),
                'hard_rejected_count': len(all_products) - len(eligible),
                'candidate_count_after_exclusions': len(after_exclusions),
                'heldout_count': len(holdout), 'heldout_ids_sha256': digest(sorted(holdout_ids)),
                'heldout_events': 0,
                'learned_weights': {key: dict(getattr(taste, f'{key}_weights') or {})
                                    for key in ('style', 'category', 'color', 'brand')},
                'control_holdout': metrics(control_holdout), 'learned_holdout': metrics(learned_holdout),
                'control_after_exclusions': metrics(control_after),
                'learned_after_exclusions': metrics(learned_after),
                'exclusion_only_change': top_change(control_before, control_after),
                'rerank_same_pool_change': top_change(control_after, learned_after),
                'heldout_rerank_change': top_change(control_holdout, learned_holdout)}
    finally:
        engine.dispose()


def run_benchmark(checkpoints=CHECKPOINTS):
    with isolated_runtime():
        from app.services.catalog.versions import NORMALIZATION_VERSION
        points = [replay_checkpoint(style, n) for style in STYLES for n in checkpoints]
    return {'benchmark': 'activation_feedback_replay_v2_dna', 'synthetic': True,
        'business_lift_measured': False, 'normalization_version': NORMALIZATION_VERSION,
        'fixture_sha256': digest([{**r, 'last_seen_in_feed_at': str(r['last_seen_in_feed_at'])} for r in fixture_rows()]),
        'scope': 'Real event→TasteProfile/User Twin→affinity and diversity code; fixed hard-eligible pool. '
                 'No candidate-retrieval, photographic relevance, conversion or causal business evaluation.',
        'control': 'Same declared preferences, candidate IDs, hard filters, exclusions and diversity; learned weights zero.',
        'evaluation': 'Disjoint fixed heldout SKU labels; training events never target heldout. '
                      'At 0/10 the real feed remains blocked; diagnostic ranks do not imply feed access.',
        'checkpoints': points}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='Optional JSON report path (no external data inputs).')
    args = parser.parse_args()
    report = run_benchmark()
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + '\n', encoding='utf-8')
    # Windows redirected stdout may use cp1251; keep the console JSON ASCII
    # while the standalone artifact remains UTF-8.
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
