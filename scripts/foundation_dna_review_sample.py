"""Read-only evidence sample; no URLs, credentials, user data or catalogue writes."""
from datetime import datetime, timezone
import json
import os
import re
import sys

sys.path.insert(0, '/app')
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from app.db import SessionLocal
from app.models import Product
from app.services.catalog.product_dna import build_product_dna, RULE_VERSION

assert make_url(os.environ['DATABASE_URL']).host == 'modish_foundation_test_pg'
hint = re.compile(r'кро[йяе]|силуэт|посадк|прям|свобод|облег|фигур|притал|оверсайз|wide|slim|fit', re.I)
result = []
with SessionLocal() as db:
    db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
    for source in ('baon', 'shoppinglive', 'serginnetti', 'postmeridiem'):
        base = (Product.source == source, Product.is_active == 1, Product.is_deleted_from_feed == 0)
        categories = db.scalars(select(Product.category).where(*base).distinct()).all()
        gaps, conflicts = [], []
        for category in sorted(categories):
            products = db.scalars(select(Product).where(*base, Product.category == category).order_by(Product.id).limit(25))
            for product in products:
                dna = build_product_dna(product)
                blocked = {dim: rows for dim, rows in dna['semantic'].items()
                           if any(row['status'] == 'conflict' for row in rows)}
                if blocked and len(conflicts) < 12:
                    conflicts.append({'id': product.id, 'title': product.title, 'category': category,
                                      'assertions': blocked, 'blocked_evidence': dna['blocked_evidence']})
                if len(gaps) < 20 and hint.search(product.title or '') and any(
                    dim in dna['unknown_dimensions'] for dim in ('fit', 'silhouette')
                ):
                    construction = {}
                    for field in ('feed_raw_json', 'raw_params_json'):
                        values = getattr(product, field, {}) or {}
                        construction[field] = {str(k): v for k, v in values.items()
                            if str(k).lower().removeprefix('param_') in {'fit', 'silhouette', 'посадка', 'силуэт', 'крой'}}
                    gaps.append({'id': product.id, 'title': product.title, 'category': category,
                                 'raw_construction': construction,
                                 'fit': dna['semantic']['fit'], 'silhouette': dna['semantic']['silhouette']})
        result.append({'source': source, 'construction_review': gaps, 'conflict_review': conflicts})
    db.rollback()
print(json.dumps({'scope': 'isolated_clone_read_only', 'rules_version': RULE_VERSION,
                  'checked_at': datetime.now(timezone.utc).isoformat(),
                  'sampling': 'same first 25 IDs per source/category as DNA audit; bounded review examples',
                  'sources': result}, ensure_ascii=False))
