"""Download four configured female-segment feeds over fixed-host HTTPS; no DB writes."""
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlsplit
import hashlib
import json
import sys
sys.path.insert(0, '/app')
import httpx
from sqlalchemy import select,text
from app.db import SessionLocal
from app.models import ProductSource

CODES = ('baon','shoppinglive','serginnetti','postmeridiem')
target = Path('/feeds')
with SessionLocal() as db:
    db.execute(text('SET TRANSACTION READ ONLY'))
    sources = {s.code:s.feed_url for s in db.scalars(select(ProductSource).where(ProductSource.code.in_(CODES)))}
    db.rollback()
assert set(sources)==set(CODES)
report = {'sources':[],'production_database_changed':False,'started_at':datetime.now(timezone.utc).isoformat()}
for code in CODES:
    parsed=urlsplit(sources[code])
    assert parsed.hostname=='export.admitad.com' and not parsed.username and parsed.scheme in {'http','https'}
    url=parsed._replace(scheme='https').geturl()
    size=0; digest=hashlib.sha256()
    with httpx.stream('GET',url,follow_redirects=False,timeout=httpx.Timeout(120,connect=20)) as response:
        assert response.status_code==200, (code,response.status_code)
        with (target/(code+'.csv')).open('wb') as output:
            for chunk in response.iter_bytes():
                size+=len(chunk)
                assert size<=256*1024*1024, 'feed_size_limit'
                digest.update(chunk);output.write(chunk)
    report['sources'].append({'code':code,'bytes':size,'sha256':digest.hexdigest()})
    print(json.dumps({'downloaded':code,'bytes':size}),file=sys.stderr,flush=True)
report['finished_at']=datetime.now(timezone.utc).isoformat()
print(json.dumps(report))
