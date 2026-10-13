"""Read-only semantic audit of a bounded real-catalogue sample on the clone."""
from collections import Counter
from datetime import datetime, timezone
import json
import os
import sys

sys.path.insert(0, '/app')
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from app.db import SessionLocal
from app.models import Product
from app.services.catalog.product_dna import build_product_dna, RULE_VERSION
from app.services.catalog.stock_freshness import stock_observation_problem

assert make_url(os.environ['DATABASE_URL']).host == 'modish_foundation_test_pg'
checked_at = datetime.now(timezone.utc)
sources = []
with SessionLocal() as db:
    db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
    for source in ('baon', 'shoppinglive', 'serginnetti', 'postmeridiem'):
        base = (Product.source == source, Product.is_active == 1, Product.is_deleted_from_feed == 0)
        categories = db.execute(select(Product.category, func.count()).where(*base).group_by(Product.category)).all()
        counts, conflicts, stock = Counter(), Counter(), Counter()
        examples = []
        sampled = 0
        for category, population in sorted(categories):
            products = db.scalars(select(Product).where(*base, Product.category == category).order_by(Product.id).limit(25))
            for product in products:
                sampled += 1
                dna = build_product_dna(product)
                stock[stock_observation_problem(product, checked_at=checked_at) or 'fresh'] += 1
                values = {}
                for dimension, rows in dna['semantic'].items():
                    supported = [row['value'] for row in rows if row['status'] == 'supported']
                    if supported:
                        counts[dimension] += 1
                        values[dimension] = supported
                    if dimension in dna['blocked_evidence'] or any(row['status'] == 'conflict' for row in rows):
                        conflicts[dimension] += 1
                if len(examples) < 12 and (values or dna['blocked_evidence']):
                    examples.append({'product_id': product.id, 'title': product.title,
                        'category': product.category, 'values': values,
                        'blocked_dimensions': list(dna['blocked_evidence'])})
        sources.append({'source': source, 'active_snapshot_skus': sum(n for _, n in categories),
            'sampled_skus': sampled, 'category_population': dict(categories),
            'supported_dimensions': dict(counts), 'conflict_dimensions': dict(conflicts),
            'stock_state_in_sample': dict(stock), 'examples': examples})
    db.rollback()
print(json.dumps({'scope': 'isolated_clone_read_only', 'rules_version': RULE_VERSION,
    'checked_at': checked_at.isoformat(),
    'sampling': 'first 25 IDs per source/category; descriptive sample, not representative coverage or eligibility',
    'sources': sources}, ensure_ascii=False))
