"""PostgreSQL smoke checks in an enforced read-only transaction."""
import json
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from app.db import engine
from app.models import Product
from app.api.products import products_list
from app.services.outfit_quality import pair_compatible


def main():
    with Session(engine, autoflush=False) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        assert db.scalar(text('SHOW transaction_read_only')) == 'on'
        report = {'transaction_read_only': True, 'queries': []}
        for query, marker in [('куртка', 'куртк'), ('КУРТКА', 'куртк'), ('джинсы', 'джинс'),
                              ('BAON', 'baon'), ('чёрные брюки', None), ('верхняя одежда', None)]:
            rows = products_list(q=query, limit=8, db=db, credentials=None)
            if marker:
                assert rows, f'No real catalogue results for {query}'
                mismatches = [{'title': p['title'], 'category': p.get('category'), 'brand': p.get('brand')}
                    for p in rows if marker not in ' '.join(str(p.get(k) or '') for k in
                    ('title','brand','category','category_name','subcategory')).lower()]
                assert not mismatches, {'query': query, 'unexpected': mismatches}
            report['queries'].append({'query': query, 'count': len(rows)})
        assert products_list(q='modish-no-such-item-9283716', db=db, credentials=None) == []
        for row in products_list(q='%', db=db, credentials=None):
            assert '%' in ' '.join(str(row.get(k) or '') for k in
                ('title','brand','category','category_name','subcategory','colors','color_original','color_family'))
        coats = list(db.scalars(select(Product).where(Product.is_active == 1,
            Product.title.ilike('%куртка%'), Product.title.ilike('%пух%')).limit(10)))
        sandals = list(db.scalars(select(Product).where(Product.is_active == 1,
            Product.title.ilike('%сандал%')).limit(10)))
        assert coats and sandals, 'Need real warm coats and sandals for regression check'
        assert all(not pair_compatible(coat, sandal) for coat in coats for sandal in sandals)
        report.update(result='passed', warm_coats=len(coats), sandals=len(sandals),
                      rejected_season_pairs=len(coats)*len(sandals))
        db.rollback()
        print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
