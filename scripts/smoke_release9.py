"""Ten real-catalog profiles, isolated restored PostgreSQL only."""
import os, sys, json, secrets, logging, time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from sqlalchemy import text, select
from sqlalchemy.engine import make_url

def main():
    url=make_url(os.environ['DATABASE_URL'])
    assert url.host=='modish_release9_test_pg' and url.database=='modish_test'
    sys.path.insert(0,'/app')
    import dotenv
    dotenv.load_dotenv=lambda *a,**k: False
    logging.disable(logging.CRITICAL)
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db import SessionLocal
    from app.models import Product, ActivationSession, FitProfile, User, RecommendationEventV2
    from app.services import activation_service as a
    from app.services.product_identity import product_identity_keys
    from app.services.garment_roles import product_role
    from app.services.outfit_quality import outfit_compatible, scenario_product_ok
    cases=[('XS','36','economy',['casual','minimalism']),('S','37','mass',['casual','smart_casual']),
        ('M','38','mass',['casual','minimalism']),('L','39','mass',['casual','smart_casual']),
        ('XL','40','mid',['smart_casual','elegant']),('42','37','mass',['casual','romantic']),
        ('44','38','mass',['minimalism','smart_casual']),('46','39','mid',['elegant','romantic']),
        ('48','40','mid',['casual','minimalism']),('S','38','mass',['romantic','elegant'])]
    report={'profiles':[],'concurrent_idempotency':False}
    with SessionLocal() as db:
        report['catalog_products']=db.scalar(text('select count(*) from products'))
        assert report['catalog_products']>180000
        baseline_views=a.activation_metrics(db)['unique_first20_views']
    with TestClient(app,raise_server_exceptions=True) as client:
        def req(method,path,**kw):
            res=client.request(method,path,**kw)
            assert res.status_code==200,f'{path}: HTTP_{res.status_code}: {res.text[:300]}'
            return res.json()
        for index,(size,shoe,budget,styles) in enumerate(cases):
            started=time.monotonic()
            auth=req('POST','/auth/register',json={'email':f'activation-{uuid4().hex}@example.com','password':secrets.token_urlsafe(24)})
            headers={'Authorization':'Bearer '+auth['access_token']}
            user=req('GET','/users/me',headers=headers); uid=user['id']
            assert client.get('/feed',headers=headers).status_code==409
            req('POST','/onboarding/preferences',headers=headers,json={'age_group':'25-34','clothing_size':size,'shoe_size':shoe,'styles':styles,'price_segment':budget})
            deck=req('GET','/onboarding/style-swipe',headers=headers)
            print(json.dumps({'progress':'deck','profile':index+1,'size':size,'count':len(deck['products'])}),flush=True)
            assert len(deck['products'])>=15,f'profile_{index+1}: insufficient honest Style Swipe cards'
            assert len({p['id'] for p in deck['products']})==len(deck['products'])
            assert client.post('/onboarding/complete',headers=headers).status_code==409
            first=deck['products'][0]['id']
            if index==0:
                with ThreadPoolExecutor(max_workers=2) as executor:
                    results=list(executor.map(lambda _:req('POST','/onboarding/style-swipe',headers=headers,json={'product_id':first,'reaction':'like'}),range(2)))
                assert all(r['rated']==1 for r in results)
                report['concurrent_idempotency']=True
            # Like matching styles, retain at least ten positive examples;
            # no fabricated catalogue data or relaxation of the size gate.
            for n,p in enumerate(deck['products'][:15]):
                reaction='dislike' if n in (3,7,11) else 'like'
                req('POST','/onboarding/style-swipe',headers=headers,json={'product_id':p['id'],'reaction':reaction})
            status=req('GET','/onboarding/status',headers=headers)
            assert status['rated']==15 and not status['completed']
            resumed=req('GET','/onboarding/style-swipe',headers=headers)
            assert resumed['rated']==15
            wow=req('POST','/onboarding/complete',headers=headers)
            retry=req('POST','/onboarding/complete',headers=headers)
            assert [r['id'] for r in wow['outfits']]==[r['id'] for r in retry['outfits']]
            feed=req('GET','/feed?limit=20',headers=headers)
            assert feed,f'profile_{index+1}: no personalized feed'
            with SessionLocal() as db:
                activation=db.get(ActivationSession,uid)
                fit=db.scalar(select(FitProfile).where(FitProfile.user_id==uid))
                seen=set()
                for row in wow['outfits']:
                    ps=[db.get(Product,pid) for pid in row['items'].values()]
                    assert {product_role(p) for p in ps} in ({'top','bottom','shoes'},{'one_piece','shoes'})
                    assert sum(p.price for p in ps)<=fit.budget_max
                    assert outfit_compatible(ps) and all(scenario_product_ok(p,row['style_direction']) for p in ps)
                    for p in ps:
                        assert a.product_ok(p,fit,activation)
                        keys=product_identity_keys(p); assert not seen & keys; seen.update(keys)
                for row in feed:
                    assert a.product_ok(db.get(Product,row['product']['id']),fit,activation)
                    assert row['ranking_algorithm']=='first_session_feed'
                assert a.activation_metrics(db)['unique_first20_views']==baseline_views+sum(r['viewed'] for r in report['profiles'])
            viewed=0
            for row in feed[:10]:
                req('POST','/recommendations/events',headers=headers,json={'product_id':row['product']['id'],'event_type':'view','meta':{'surface':'feed'}})
                req('POST','/recommendations/events',headers=headers,json={'product_id':row['product']['id'],'event_type':'like'})
                viewed+=1
            reranked=req('GET','/feed',params=[('limit','20')]+[('exclude_ids',r['product']['id']) for r in feed[:10]],headers=headers)
            assert not ({r['product']['id'] for r in reranked}&{r['product']['id'] for r in feed[:10]})
            check={'profile':index+1,'size':size,'shoe':shoe,'budget':budget,'styles':styles,'deck':len(deck['products']),
                'wow':len(wow['outfits']),'feed':len(feed),'reranked':len(reranked),'viewed':viewed,'seconds':round(time.monotonic()-started,2),
                'preview':{'swipe':deck['products'][:6],'outfits':wow['outfits'],'feed':feed[:6]}}
            report['profiles'].append(check)
            print(json.dumps({'progress':'profile_passed',**{k:v for k,v in check.items() if k!='preview'}}),flush=True)
    with SessionLocal() as db:
        report['test_metrics']=a.activation_metrics(db)
    assert sum(p['wow']>0 for p in report['profiles'])>=8,'insufficient_complete_outfits_across_profiles'
    report['result']='passed'
    print(json.dumps(report),flush=True)

if __name__=='__main__':
    try:main()
    except Exception as exc:
        print(json.dumps({'result':'failed','error':str(exc)}),flush=True)
        raise
