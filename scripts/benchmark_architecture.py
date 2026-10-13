"""Offline CPU/query benchmark; no production I/O, credentials or load claims."""
import argparse
from collections import Counter
import json
from pathlib import Path
from statistics import median
from time import perf_counter
from types import SimpleNamespace

from benchmark_activation_feedback import AS_OF, digest, isolated_runtime


def run(*, optimized=False, count=1000):
    with isolated_runtime():
        from sqlalchemy import create_engine, event
        from sqlalchemy.orm import Session
        from app.db import Base
        from app.models import Product, TasteProfile, User
        from app.services.activation_service import _affinity
        from app.services.mie_scoring import compute_taste_score
        from app.services.recommendation_context import ShoppingContext
        from app.services.user_twin_service import DNA_TASTE_PREFIX, apply_event_to_user_twin
        products = [Product(id=str(i), external_id=str(i), source='baon', brand='BAON',
            title='Хлопковая рубашка', category='рубашки', price=2000+i%10,
            colors=['black'], style_tags=['casual'],
            feed_raw_json={'material': 'cotton' if i%2 else 'linen', 'fit': 'regular',
                'pattern': 'striped', 'occasion': 'work', 'formality': 'smart'},
            image_url='https://example.test/p.png', product_url='https://example.test/'+str(i))
            for i in range(count)]
        profile = SimpleNamespace(style_preferences=['casual', 'minimalism'])
        taste = TasteProfile(category_weights={'рубашки': 3}, brand_weights={}, color_weights={}, style_weights={})
        twin = {('item', 'old-'+str(i)): (.1, .3) for i in range(1000)}
        twin.update({('material', DNA_TASTE_PREFIX+'cotton'): (.9, .8), ('category', 'рубашки'): (.5, .6)})
        context = ShoppingContext('office')
        timings = []
        outputs = None
        for _ in range(3):
            started = perf_counter()
            if optimized:
                from app.services.personalization_scoring import AffinityScorer
                scorer = AffinityScorer(profile, taste, taste_features=twin, context=context)
                outputs = [(scorer.evaluate(p).affinity, scorer.evaluate(p).taste_score,
                            scorer.evaluate(p).context_score) for p in products]
            else:
                outputs = [(_affinity(p, profile, taste, taste_features=twin, context=context),
                    compute_taste_score(p, taste=taste, taste_features=twin)[0], context.score(p)[0])
                    for p in products]
            timings.append(round(perf_counter()-started, 6))
        engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(engine)
        statements = Counter()
        def record(_conn, _cursor, statement, _params, _context, _many):
            statements[statement.split()[0].upper()] += 1
        with Session(engine, autoflush=False) as db:
            db.add(User(id='u', email='benchmark@example.test', password_hash='unused'))
            db.add(products[0]); db.flush()
            event.listen(engine, 'before_cursor_execute', record)
            features = apply_event_to_user_twin(db, user_id='u', product=products[0], event_type='like', occurred_at=AS_OF)
            event.remove(engine, 'before_cursor_execute', record)
            touched = len(features)
            db.rollback()
        engine.dispose()
    return {'fixture': '1000_products_1002_taste_features_v1', 'products': count,
        'optimized': optimized, 'timings_seconds': timings, 'median_seconds': median(timings),
        'scores_sha256': digest(outputs), 'event_touched_features': touched,
        'event_sql_statements': dict(statements), 'scope': 'offline CPU and SQLite query count; not production throughput or p95'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--optimized', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = run(optimized=args.optimized)
    args.output.write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(result))
