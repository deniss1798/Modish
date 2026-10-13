"""Read only: check configured feed response headers and a bounded content prefix."""
import json
import sys
from urllib.parse import urlsplit
sys.path.insert(0, '/app')
import httpx
from sqlalchemy import select, text
from app.db import SessionLocal
from app.models import ProductSource
from app.services.catalog.affiliate_link_service import _public_web_url

with SessionLocal() as db:
    db.execute(text('SET TRANSACTION READ ONLY'))
    sources = [(s.code, s.feed_url) for s in db.scalars(select(ProductSource).order_by(ProductSource.code))]
    db.rollback()
report = {'scope':'configured_feed_bounded_read_only_probe','database_changed':False,'sources':[]}
for code, url in sources:
    result = {'code':code,'configured':bool(url)}
    # Old configured exports may use HTTP; credentials are sent only over HTTPS.
    if url and urlsplit(url).hostname == 'export.admitad.com' and urlsplit(url).scheme == 'http':
        url = urlsplit(url)._replace(scheme='https').geturl()
    if not _public_web_url(url) or urlsplit(url).scheme != 'https' or urlsplit(url).hostname != 'export.admitad.com':
        result['result'] = 'missing_or_invalid_url'
    else:
        result['host'] = urlsplit(url).hostname
        try:
            with httpx.stream('GET', url, timeout=httpx.Timeout(20,connect=10), follow_redirects=False) as response:
                result['http_status'] = response.status_code
                result['content_type'] = response.headers.get('content-type','').split(';')[0]
                prefix = next(response.iter_bytes(chunk_size=8192), b'').decode('utf-8',errors='replace').lstrip('\ufeff \r\n')
                result['looks_like_catalog'] = bool(prefix and ('<yml_catalog' in prefix[:2000]
                    or '<offers' in prefix[:2000] or ('available' in prefix[:1000] and 'price' in prefix[:1000])))
                result['result'] = 'catalog_prefix_received' if response.status_code == 200 and result['looks_like_catalog'] else 'not_verified'
        except Exception as exc:
            result['result'] = 'request_failed'
            result['error_type'] = type(exc).__name__
    report['sources'].append(result)
    print(json.dumps({'progress':result}), file=sys.stderr, flush=True)
print(json.dumps(report))
