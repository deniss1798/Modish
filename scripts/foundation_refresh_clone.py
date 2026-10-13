"""Import downloaded source snapshots only on the fixed isolated PostgreSQL clone."""
from pathlib import Path
from datetime import datetime,timedelta,timezone
import json
import os
import sys
sys.path.insert(0,'/app')
from sqlalchemy import select,update
from sqlalchemy.engine import make_url
from app.db import SessionLocal
from app.models import Product,ProductSource
from app.services.catalog.feed_import_service import sync_partner_feed_from_text
assert make_url(os.environ['DATABASE_URL']).host=='modish_foundation_test_pg'
report={'sources':[],'scope':'fresh_four_sources_isolated_clone','result':'running'}
with SessionLocal() as db:
    for code in ('baon','shoppinglive','serginnetti','postmeridiem'):
        source=db.scalar(select(ProductSource).where(ProductSource.code==code))
        run=sync_partner_feed_from_text(db,source=source,
            body=Path('/feeds/'+code+'.csv').read_text(encoding='utf-8-sig'),parser_url_hint='feed.csv')
        # No raw exceptions/URLs: credentials must not appear in validation output.
        assert run.status=='ok', ('import_failed',code,run.id)
        item={'code':code,'rows':run.total_received,'created':run.created_count,'updated':run.updated_count,
              'quarantined':run.skipped_count,'reasons':run.skip_breakdown,'missing':run.deactivated_count}
        report['sources'].append(item)
        print(json.dumps({'progress':item}),flush=True)
    # Explicit test cohort: old snapshots remain stored, but cannot populate the
    # fresh-catalog acceptance test. This is not a production expiry scheduler.
    result=db.execute(update(Product).where(Product.last_seen_in_feed_at<datetime.now(timezone.utc)-timedelta(hours=72))
        .values(is_active=0,is_available=0))
    report['stale_excluded_in_clone']=result.rowcount
    db.commit()
report['result']='passed'
print(json.dumps(report))
