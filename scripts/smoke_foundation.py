"""Exercise real merchant records and replay only inside the isolated clone."""
from collections import Counter
from datetime import datetime, timezone
import json
import os
import sys

sys.path.insert(0, '/app')
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from app.db import SessionLocal
from app.models import CatalogRecord, Product, ProductSource
from app.services.catalog.catalog_records import prepare_record, reprocess_records, store_record


def main():
    url = make_url(os.environ['DATABASE_URL'])
    assert url.host == 'modish_foundation_test_pg' and url.database == 'modish_test'
    now = datetime.now(timezone.utc)
    report = {'scope':'stratified_real_rows_on_isolated_clone', 'sources':[], 'result':'running'}
    with SessionLocal() as db:
        before = db.scalar(select(func.count()).select_from(Product))
        for source in db.scalars(select(ProductSource).order_by(ProductSource.code)).all():
            selected, strata, scanned = [], Counter(), 0
            # Audit covers every row. The mutation smoke uses a bounded, reproducible
            # UUID-ordered sample and up to three examples per observed stratum.
            for p in db.scalars(select(Product).where(Product.source == source.code,
                Product.is_deleted_from_feed == 0).order_by(Product.id).limit(1000).execution_options(yield_per=200)):
                scanned += 1
                if not p.feed_raw_json:
                    continue
                _, _, payload, canonical, decision = prepare_record(p.feed_raw_json, source, now)
                if payload is None or payload['external_id'] != p.external_id:
                    continue
                key = (decision.status, tuple(decision.reasons), canonical.get('size_system'))
                if strata[key] < 3:
                    selected.append((p.id, p.feed_raw_json, p.last_seen_in_feed_at or p.updated_at))
                    strata[key] += 1
            counts = Counter()
            for pid, raw, observed in selected:
                record, action = store_record(db, source, raw, now, last_seen_at=observed)
                assert record.product_id == pid, 'identity_must_be_preserved'
                rid, digest = record.id, record.raw_hash
                again, _ = store_record(db, source, raw, now, last_seen_at=observed)
                assert (again.id, again.product_id, again.raw_hash) == (rid, pid, digest)
                product = db.get(Product, pid)
                if record.status != 'passed':
                    assert not product.is_active and not product.is_available
                assert product.last_seen_in_feed_at == observed
                counts[record.status] += 1
            db.commit()
            cursor, processed = None, 0
            while selected:
                result = reprocess_records(db, source, dry_run=False, limit=25, after_id=cursor)
                processed += result['processed']
                db.commit()
                if not result['has_more']:
                    break
                cursor = result['next_after_id']
            assert processed == len(selected)
            item = {'source':source.code,'scanned':scanned,'sample':len(selected),'counts':dict(counts),'replayed':processed}
            report['sources'].append(item)
            print(json.dumps({'progress':'source_passed',**item}), flush=True)
        assert db.scalar(select(func.count()).select_from(Product)) == before
        assert db.scalar(select(func.count()).select_from(CatalogRecord)) == sum(s['sample'] for s in report['sources'])
    report['result'] = 'passed'
    report['products_unchanged_count'] = before
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
