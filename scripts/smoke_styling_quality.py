"""Retest garment classification and all outfit scenarios after the long feed test."""
import os,sys,json,logging
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.engine import make_url
url=make_url(os.environ['DATABASE_URL'])
assert url.host=='modish_styling_test_pg' and url.database=='modish_test'
sys.path.insert(0,'/app');os.environ['RATE_LIMIT_PER_MINUTE']='100000'
import dotenv
dotenv.load_dotenv=lambda *a,**k:False
logging.disable(logging.CRITICAL)
from fastapi.testclient import TestClient
from app.main import app
from app.db import SessionLocal
from app.models import User,FitProfile,Product
from app.api.deps import create_access_token
from app.services.garment_roles import product_role
from app.services.catalog_audience import product_is_adult
from app.services.outfit_quality import scenario_product_ok,outfit_compatible
from app.services.outfit_engine_v2 import _hard_ok
from app.services.catalog.rule_filters import load_rules_by_source_id
prior=json.loads(Path('/prior.json').read_text())
report=prior['smoke']
report['long_test_image_id']=prior['image_id']
report['outfits']=[]
report['final_quality_checks']=[]
with TestClient(app,raise_server_exceptions=True) as client:
 for gender in ('menswear','womenswear'):
  with SessionLocal() as db:
   u=db.scalars(select(User).join(FitProfile,FitProfile.user_id==User.id).where(
    User.email.like('styling-%@example.com'),FitProfile.gender_target==gender).order_by(User.created_at.desc())).first()
   assert u is not None
   headers={'Authorization':'Bearer '+create_access_token(u.id)}
  response=client.get('/feed?limit=30',headers=headers);assert response.status_code==200 and len(response.json())==30
  for scenario in ('daily','office','evening'):
   response=client.post('/outfits/generate',params={'scenario':scenario,'count':3},headers=headers)
   assert response.status_code==200
   outfits=response.json();assert outfits,f'{gender}:{scenario}:empty'
   with SessionLocal() as db:
    for row in outfits:
     products=[db.get(Product,p['id']) for p in row['products'].values()]
     roles={product_role(p) for p in products}
     assert roles in ({'top','bottom','shoes'},{'one_piece','shoes'})
     assert all(product_is_adult(p) and scenario_product_ok(p,scenario) for p in products)
     assert outfit_compatible(products)
     report['outfits'].append({'gender':gender,'scenario':scenario,'items':[{'title':p.title,'role':product_role(p)} for p in products]})
   report['final_quality_checks'].append({'gender':gender,'scenario':scenario,'outfits':len(outfits)})
   print(json.dumps({'progress':'final_outfits','gender':gender,'scenario':scenario,'count':len(outfits)}),flush=True)
  if gender=='menswear':
   with SessionLocal() as db:
    fit=db.scalars(select(FitProfile).where(FitProfile.user_id==u.id)).one()
    rules=load_rules_by_source_id(db)
    anchor=next((p for p in db.scalars(select(Product).where(Product.title.ilike('%Майка%DC Shoes%')))
      if _hard_ok(p,fit=fit,rules_by_source=rules)),None)
    assert anchor and product_role(anchor)=='top'
    response=client.get(f'/products/{anchor.id}/complements',headers=headers)
    assert response.status_code==200
    related=response.json();assert related
    assert all(p['outfit_slot'] in ('bottom','shoes') for p in related)
    report['dc_shoes_tank_complements']={'count':len(related),'roles':sorted({p['outfit_slot'] for p in related})}
report['result']='passed'
print(json.dumps(report,ensure_ascii=False),flush=True)
