"""Read-only size-system evidence, only from the isolated fresh clone."""
import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
sys.path.insert(0,'/app')
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from app.db import SessionLocal
from app.models import Product
assert make_url(os.environ['DATABASE_URL']).host=='modish_foundation_test_pg'
report=[]
with SessionLocal() as db:
    db.execute(text('SET TRANSACTION READ ONLY'))
    for source in ('baon','shoppinglive','serginnetti','postmeridiem'):
        systems=Counter(); examples={}; count=0
        for p in db.scalars(select(Product).where(Product.source==source,Product.is_active==1,
                Product.last_seen_in_feed_at>=datetime.now(timezone.utc)-timedelta(hours=72))):
            count+=1
            systems[(p.category,p.size_system or 'unknown')]+=1
            if len(examples.get(p.category,[]))>=2: continue
            sizes={str(k):str(v)[:80] for k,v in (p.feed_raw_json or {}).items()
                   if ('size' in str(k).lower() or 'размер' in str(k).lower()) and 'http' not in str(v)}
            examples.setdefault(p.category,[]).append({'sizes':p.available_sizes,'original':p.size_original,
                 'system':p.size_system,'raw_size_fields':sizes})
        report.append({'source':source,'active':count,'systems':[{'category':c,'system':s,'count':n} for (c,s),n in systems.items()],
                       'examples':examples})
    db.rollback()
print(json.dumps({'scope':'isolated_clone_read_only','sources':report},ensure_ascii=False))
