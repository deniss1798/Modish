"""Read-only production snapshot: aggregate raw quality and first-session coverage.

No user data, feed URLs or affiliate URLs are emitted. CatalogRecord is not queried,
so the prospective normalizer can be audited before its migration is applied.
"""
from collections import Counter
from datetime import datetime, timezone
import json
import sys
from types import SimpleNamespace

sys.path.insert(0, '/app')
from sqlalchemy import select, text
from app.db import SessionLocal
from app.models import Product, ProductSource
from app.services.catalog.catalog_records import prepare_record
from app.services.catalog.rule_filters import load_rules_by_source_id
from app.services.activation_service import product_ok
from app.services.garment_roles import product_role
from app.catalog_normalize import normalize_size_token

SIZES = ('XS', 'S', 'M', 'L', 'XL')
BUDGETS = (4000, 10000, 25000)


def coverage(product, rules):
    if product.gender_target != 'womenswear' or not 300 <= int(product.price or 0) <= 25000:
        return []
    sizes = {normalize_size_token(str(s)) for s in (product.available_sizes or [])}
    shoes = product_role(product) == 'shoes'
    matching = list(SIZES) if shoes and '38' in sizes else [] if shoes else [s for s in SIZES if s in sizes]
    if not matching:
        return []
    # All other hard constraints are shared across the 15 audit profiles.
    fit = SimpleNamespace(gender_target='womenswear', clothing_size=matching[0], budget_max=25000)
    if not product_ok(product, fit, SimpleNamespace(shoe_size='38'), rules=rules):
        return []
    return [f'{size}/{budget}' for size in matching for budget in BUDGETS if product.price <= budget]


def main():
    now = datetime.now(timezone.utc)
    report = {'generated_at': now.isoformat(), 'scope': 'stored_product_raw_only',
        'transaction_read_only': True, 'network_urls_verified': False,
        'coverage_unit': 'SKU rows, not distinct styles; shoe size 38 in each profile', 'sources': []}
    with SessionLocal() as db:
        db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
        assert db.scalar(text('SHOW transaction_read_only')) == 'on'
        rules = load_rules_by_source_id(db)
        sources = list(db.scalars(select(ProductSource).order_by(ProductSource.code)))
        for source in sources:
            stats = {key: Counter() for key in ('counts', 'reasons', 'active_reasons', 'stock_signals',
                'size_systems', 'coverage_before', 'coverage_after', 'categories_after', 'freshness_hours')}
            examples = {}
            query = select(Product).where(Product.source == source.code).order_by(Product.id)
            for product in db.scalars(query.execution_options(yield_per=200)):
                stats['counts']['products'] += 1
                active = bool(product.is_active and product.is_available and not product.is_deleted_from_feed)
                stats['counts']['active'] += int(active)
                stats['coverage_before'].update(coverage(product, rules))
                row = product.feed_raw_json
                if not isinstance(row, dict) or not row:
                    stats['counts']['raw_missing'] += 1
                    continue
                raw, _, payload, canonical, decision = prepare_record(row, source, now)
                stats['counts'][decision.status] += 1
                stats['reasons'].update(decision.reasons)
                if active:
                    stats['active_reasons'].update(decision.reasons)
                    stats['counts']['active_'+decision.status] += 1
                for key, value in raw.items():
                    if str(key).lower() in {'available','availability','stock','instock','in_stock'}:
                        stats['stock_signals'][str(key)+':'+str(value)[:60]] += 1
                if canonical:
                    stats['size_systems'][canonical.get('size_system') or 'unknown'] += 1
                if product.last_seen_in_feed_at:
                    hours = (now-product.last_seen_in_feed_at.replace(tzinfo=timezone.utc)).total_seconds()/3600
                    stats['freshness_hours']['0-24' if hours <= 24 else '24-72' if hours <= 72 else '72+'] += 1
                else:
                    stats['freshness_hours']['unknown'] += 1
                if payload and not product.is_deleted_from_feed:
                    proposed = Product(**payload)
                    stats['coverage_after'].update(coverage(proposed, rules))
                    if decision.status == 'passed':
                        stats['categories_after'][payload['category']] += 1
                for reason in decision.reasons:
                    bucket = ('active:' if active else 'inactive:') + reason
                    if len(examples.setdefault(bucket, [])) < 4:
                        examples[bucket].append({'title': product.title, 'external_id': product.external_id,
                            'sizes_before': product.available_sizes,
                            'sizes_after': (canonical or {}).get('available_sizes'),
                            'category_after': (canonical or {}).get('category'),
                            'price_before': product.price, 'price_after': (canonical or {}).get('price')})
            report['sources'].append({'code': source.code, 'status': source.status,
                'last_sync_at': source.last_sync_at.isoformat() if source.last_sync_at else None,
                **{k: dict(v) for k, v in stats.items()}, 'examples': examples})
            print(json.dumps({'source_complete': source.code, 'rows': stats['counts']['products']}),
                  file=sys.stderr, flush=True)
        db.rollback()
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
