"""Style Swipe followed by a first-session feed, only on the test PostgreSQL."""
from datetime import datetime, timezone
import json,os,sys
from time import perf_counter
from uuid import uuid4
sys.path.insert(0,'/app')
from sqlalchemy import select,text
from sqlalchemy.engine import make_url
assert make_url(os.environ['DATABASE_URL']).host=='modish_foundation_test_pg'
assert make_url(os.environ['DATABASE_URL']).database=='modish_test'
from app.db import SessionLocal
from app.models import User,FitProfile
from app.services import activation_service as activation
from app.services import shopping_intent_service as intents
from app.services.intent_metrics import summary
from app.services.product_eligibility import evaluate_eligibility
from app.services.catalog_filters import CatalogFilters
report={'scope':'isolated_clone_after_style_swipe','image_id':os.environ['AUDIT_IMAGE_ID'],
    'archive_sha256':os.environ['AUDIT_ARCHIVE_SHA256'],'profiles':[],'test_data_rolled_back':True}
configs=[('RU',s,str(36+i%5),'mid') for i,s in enumerate(('42','44','46','48','50'))]
configs+=[('LETTER',s,str(36+i%5),'mass') for i,s in enumerate(('XS','S','M','L','XL'))]
with SessionLocal() as db:
 db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ'))
 for index,(system,size,shoe,segment) in enumerate(configs,1):
  started=perf_counter()
  user=User(id=str(uuid4()),email='warm-intent-audit-'+uuid4().hex+'@example.test',password_hash='disabled-test-only')
  db.add(user);db.flush()
  session=activation.configure(db,user,age_group='25-34',height=168,clothing_size=size,
      clothing_size_system=system,shoe_size=shoe,shoe_size_system='EU',styles=['casual','minimalism'],price_segment=segment)
  deck=activation.swipe_deck(db,user,session)
  row={'index':index,'clothing_system':system,'clothing_size':size,'shoe_size':shoe,'segment':segment,
      'bootstrap_cards':len(deck['products']),'activation_completed':False,'feed_cards':None,'filters':{}}
  if len(deck['products'])>=15:
   for i,product in enumerate(deck['products'][:15]):
    activation.swipe_feedback(db,user,session,product['id'],'like' if i<10 else 'dislike')
   wow=activation.complete_wow(db,user,session)
   assert wow['completed'] and activation.rated_count(session)>=15
   row.update(activation_completed=True,style_swipe_rated=activation.rated_count(session),wow_outfits=len(wow['outfits']))
   fit=db.scalar(select(FitProfile).where(FitProfile.user_id==user.id))
   begin=perf_counter();feed=activation.personalized_feed(db,user,session,limit=20)
   row.update(feed_cards=len(feed),feed_seconds=round(perf_counter()-begin,3))
   assert all(evaluate_eligibility(r.product,fit).eligible for r in feed)
   for label,key in (('shoes','shoes'),('outerwear','outerwear')):
    result=activation.personalized_feed(db,user,session,limit=20,filters=CatalogFilters(categories=(key,)))
    row['filters'][label]=len(result)
    assert all(evaluate_eligibility(r.product,fit).eligible for r in result)
   intent=intents.create_intent(db,user,text='Брюки до 7000',request_id=uuid4().hex)
   result=intents.intent_response(db,user,intent,remember=True)
   row['intent_trousers']=len(result['products'])
  else:
   row['reason']='not_enough_eligible_bootstrap_cards'
  row['seconds']=round(perf_counter()-started,3);report['profiles'].append(row)
  print('catalog_warm_profile_completed '+str(index),file=sys.stderr,flush=True)
 db.flush()
 metrics=summary(db)
 report['postgresql_metrics_query_passed']=True
 report['synthetic_metrics_cohort']=metrics['intent_funnel']['cohort_intents']
 db.rollback()
report['result']='passed'
report['after_swipe_feed_profiles']=sum(p['activation_completed'] and p['feed_cards']>=10 for p in report['profiles'])
print(json.dumps(report),flush=True)
