"""Exact-size and intent acceptance on the isolated real catalogue only.

Synthetic users and tasks are rolled back. Feed observation timestamps are never
refreshed or fabricated by this audit. Output contains aggregate catalogue facts.
"""
from collections import Counter
from datetime import datetime, timezone
import json
import os
import sys
from time import perf_counter
from uuid import uuid4
sys.path.insert(0,'/app')
from sqlalchemy import select, func, text
from sqlalchemy.engine import make_url
assert make_url(os.environ['DATABASE_URL']).host=='modish_foundation_test_pg'
assert make_url(os.environ['DATABASE_URL']).database=='modish_test'
from app.db import SessionLocal
from app.models import Product, User, FitProfile, TasteProfile
from app.services import activation_service as activation
from app.services import shopping_intent_service as intents
from app.services.catalog.size_resolver import resolve_product_sizes
from app.services.catalog.stock_freshness import available_product_conditions
from app.services.fit_size_profile import size_profile_to_api
from app.services.product_eligibility import evaluate_eligibility
from app.services.intent_eligibility import intent_product_problem
from app.services.intent_outfits import outfit_problem
from app.schemas.shopping_intent import IntentSpec
from app.services.catalog_filters import CatalogFilters

checked=datetime.now(timezone.utc)
report={'scope':'isolated_postgresql_catalogue','checked_at':checked.isoformat(),
    'archive_sha256':os.environ['AUDIT_ARCHIVE_SHA256'],'image_id':os.environ['AUDIT_IMAGE_ID'],
    'synthetic_users_rolled_back':True,'stock_timestamps_modified':False,'profiles':[]}
with SessionLocal() as db:
    # No concurrent import changes the audited catalogue halfway through a profile.
    db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ'))
    report['sources']=[{'source':source,'stored':count,'active':active,'newest_observation':seen.isoformat() if seen else None}
        for source,count,active,seen in db.execute(select(Product.source,func.count(),func.sum(Product.is_active),
            func.max(Product.last_seen_in_feed_at)).group_by(Product.source).order_by(Product.source))]
    source_sizes={};category_sizes={};fresh_count=0
    for product in db.scalars(select(Product).where(*available_product_conditions(checked_at=checked)).execution_options(yield_per=200)):
        fresh_count+=1
        resolved=resolve_product_sizes(product)
        key='known' if any(s.known and s.availability=='available' for s in resolved) else 'unknown_or_unavailable'
        source_sizes.setdefault(product.source,Counter())[key]+=1
        category_sizes.setdefault(product.category or 'unknown',Counter())[key]+=1
    report['fresh_available_skus']=fresh_count
    report['source_size_evidence']={s:dict(c) for s,c in source_sizes.items()}
    report['category_size_evidence']={s:dict(c) for s,c in category_sizes.items()}
    configs=[('RU',s,str(36+i%5),'mid') for i,s in enumerate(('42','44','46','48','50'))]
    configs += [('LETTER',s,str(36+i%5),'mass') for i,s in enumerate(('XS','S','M','L','XL'))]
    for index,(system,size,shoe,segment) in enumerate(configs,1):
        started=perf_counter()
        user=User(id=str(uuid4()),email='intent-audit-'+uuid4().hex+'@example.test',password_hash='disabled-isolated-audit')
        db.add(user);db.flush()
        session=activation.configure(db,user,age_group='25-34',height=168,
            clothing_size=size,clothing_size_system=system,shoe_size=shoe,shoe_size_system='EU',
            styles=['casual','minimalism'],price_segment=segment)
        session.completed_at=checked;db.flush()
        fit=db.scalar(select(FitProfile).where(FitProfile.user_id==user.id))
        taste=db.scalar(select(TasteProfile).where(TasteProfile.user_id==user.id))
        taste_before=(dict(taste.style_weights or {}),dict(taste.color_weights or {}),taste.profile_version,size_profile_to_api(fit))
        feed=activation.personalized_feed(db,user,session,limit=20)
        filter_counts={}
        for category,filter_key in (('обувь','shoes'),('верхний_слой','outerwear')):
            selected=activation.personalized_feed(db,user,session,limit=20,filters=CatalogFilters(categories=(filter_key,)))
            filter_counts[category]=len(selected)
            assert all(evaluate_eligibility(r.product,fit).eligible for r in selected)
        tasks=[]
        for query in ('Брюки до 7000','Платье до 8000','Образ в офис до 20000','Образ на свадьбу до 20000 без платья и каблуков'):
            begin=perf_counter()
            intent=intents.create_intent(db,user,text=query,request_id=uuid4().hex)
            response=intents.intent_response(db,user,intent,remember=True)
            spec=IntentSpec.model_validate(response['constraints'])
            invalid=0
            for row in response['products']:
                invalid+=bool(intent_product_problem(db.get(Product,row['product']['id']),fit,spec))
            for row in response['outfits']:
                group={slot:db.get(Product,pid) for slot,pid in row['items'].items()}
                invalid+=bool(outfit_problem(group,fit,spec))
            assert invalid==0
            tasks.append({'query_kind':spec.intent_type,'categories':spec.requested_categories,
                'occasion':spec.occasion,'no_heel':bool(spec.excluded_features.get('heel')),
                'products':len(response['products']),'outfits':len(response['outfits']),
                'empty':not(response['products'] or response['outfits']),'seconds':round(perf_counter()-begin,3),
                'invalid_returned':invalid})
        assert taste_before==(dict(taste.style_weights or {}),dict(taste.color_weights or {}),taste.profile_version,size_profile_to_api(fit))
        assert all(evaluate_eligibility(row.product,fit).eligible for row in feed)
        row={'index':index,'clothing_size':size,'clothing_system':system,'shoe_size':shoe,
            'shoe_system':'EU','segment':segment,'budget':fit.budget_max,'feed_cards':len(feed),
            'filters':filter_counts,'tasks':tasks,'permanent_taste_unchanged':True,
            'seconds':round(perf_counter()-started,3)}
        report['profiles'].append(row)
        print('catalog_profile_completed '+str(index),file=sys.stderr,flush=True)
    db.rollback()
report['checks_passed']=True
report['release_gate']={'all_profiles_feed_at_least_10':all(p['feed_cards']>=10 for p in report['profiles']),
    'all_profiles_shoes_and_outerwear':all(all(p['filters'].values()) for p in report['profiles']),
    'all_profiles_have_office_outfit':all(any(t['occasion']=='office' and t['outfits'] for t in p['tasks']) for p in report['profiles']),
    'all_profiles_wedding_no_heel':all(any(t['no_heel'] and t['outfits'] for t in p['tasks']) for p in report['profiles']),
    'no_invalid_returned':True}
report['product_ready']=all(report['release_gate'].values())
print(json.dumps(report,ensure_ascii=False),flush=True)
