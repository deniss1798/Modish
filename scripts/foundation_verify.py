"""Retest the final package and prove replay cannot renew stock on the existing clone."""
from pathlib import Path
import json
import subprocess

R = Path('/opt/modish-releases/20260922-foundation')
TAG = 'modish-backend:foundation-20260922'
PG = 'modish_foundation_test_pg'
NETWORK = 'modish-foundation-test'
def inspect(name):
    return json.loads(subprocess.check_output(['docker','inspect',name]))[0]
assert inspect('modish_backend')['Image'] == 'sha256:420c4ff4467bd995cfd11f9598c0f90988e79c1e4faf33d004f11f69c87c201f'
assert set(inspect(PG)['NetworkSettings']['Networks']) == {NETWORK}
assert not inspect(PG)['HostConfig']['Binds']
assert json.loads((R/'stage-report.json').read_text())['result'] == 'passed'
subprocess.run(['python3','-u',str(R/'audit-runner.py'),'--build-only'],check=True)
code = '''
from collections import Counter
from datetime import datetime,timezone,timedelta
import json,os
from sqlalchemy import select,func,text
from sqlalchemy.engine import make_url
from app.db import SessionLocal
from app.models import CatalogRecord,Product,ProductSource
from app.services.catalog.catalog_records import reprocess_records
from app.services.catalog.versions import NORMALIZATION_VERSION
assert NORMALIZATION_VERSION=='catalog_rules_v2', ('loaded_rules',NORMALIZATION_VERSION)
assert make_url(os.environ['DATABASE_URL']).host=='modish_foundation_test_pg'
report={'result':'running','sources':[]}
with SessionLocal() as db:
 before=db.scalar(select(func.count()).select_from(Product))
 ids=set(db.scalars(select(CatalogRecord.id)))
 for source in db.scalars(select(ProductSource)).all():
  cursor=None;count=0;reasons=Counter()
  while True:
   r=reprocess_records(db,source,dry_run=False,limit=25,after_id=cursor)
   count+=r['processed'];reasons.update(r['reasons']);db.commit()
   if not r['has_more']:break
   cursor=r['next_after_id']
  report['sources'].append({'source':source.code,'replayed':count,'reasons':dict(reasons)})
 assert ids==set(db.scalars(select(CatalogRecord.id)))
 assert before==db.scalar(select(func.count()).select_from(Product))
 for record in db.scalars(select(CatalogRecord)):
  p=db.get(Product,record.product_id)
  assert not p.is_active,'old_snapshot_must_not_be_reactivated'
  assert record.last_seen_at < datetime.now(timezone.utc)-timedelta(hours=72)
  assert record.normalization_version=='catalog_rules_v2', ('record_version',record.status,record.normalization_version)
 report['record_ids_preserved']=len(ids)
 report['products_count_preserved']=before
 report['unregistered_sources']=[{'source':r[0],'products':r[1],'active':r[2],'raw_rows':r[3]} for r in db.execute(text("SELECT p.source,count(*),sum(p.is_active),count(p.feed_raw_json) FROM products p LEFT JOIN product_sources s ON s.code=p.source WHERE s.id IS NULL GROUP BY p.source ORDER BY p.source"))]
report['result']='passed'
print(json.dumps(report))
'''
with (R/'freshness-verify.log').open('w') as log:
    subprocess.run(['docker','run','--rm','--network',NETWORK,'--env-file',str(R/'test-api.env'),
        '--entrypoint','python',TAG,'-c',code],check=True,stdout=log,stderr=subprocess.STDOUT)
report = json.loads((R/'freshness-verify.log').read_text().splitlines()[-1])
report['image_id'] = inspect(TAG)['Id']
report['production_unchanged'] = inspect('modish_backend')['Image'] == 'sha256:420c4ff4467bd995cfd11f9598c0f90988e79c1e4faf33d004f11f69c87c201f'
assert report['production_unchanged']
(R/'freshness-report.json').write_text(json.dumps(report,indent=2))
print('final_package_and_freshness_passed',flush=True)
