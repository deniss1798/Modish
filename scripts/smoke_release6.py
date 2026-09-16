"""Only synthetic users in an isolated PostgreSQL restore; never live users."""
import os, sys, json, time, secrets, logging
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4
from sqlalchemy import select, func, text
from sqlalchemy.engine import make_url

def main():
    url=make_url(os.environ['DATABASE_URL'])
    assert url.host=='modish_release6_test_pg' and url.database=='modish_test', 'isolated_database_required'
    os.environ['RATE_LIMIT_PER_MINUTE']='100000'
    sys.path.insert(0,'/app')
    import dotenv
    dotenv.load_dotenv=lambda *a,**k:False
    logging.disable(logging.CRITICAL)
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db import SessionLocal
    from app.models import Product, ProductEmbedding
    from app.services.product_identity import product_identity_keys
    from app.services.catalog_scope import product_is_fashion
    from app.services.catalog_audience import product_is_adult
    from app.services.garment_roles import product_role
    from app.services.product_complements import COMPLEMENT_ROLES
    from app.services.outfit_quality import pair_compatible, outfit_compatible, scenario_product_ok
    from app.services.embedding_service import upsert_product_embedding
    report={'checks':[], 'outfits':[], 'complements':[]}
    with SessionLocal() as db:
        report['catalog_products']=db.scalar(text('select count(*) from products'))
        assert report['catalog_products']>180000
        pid=str(uuid4())
        db.add(Product(id=pid,external_id=pid,source='test',brand='Test',title='Concurrent test garment',category='рубашки',
            price=1000,product_url='https://example.test/item',image_url='https://example.test/item.jpg',is_active=0))
        db.commit()
    barrier=Barrier(8)
    def write_embedding(_):
        with SessionLocal() as db:
            p=db.get(Product,pid);barrier.wait(timeout=30)
            upsert_product_embedding(db,p);db.commit()
    with ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(write_embedding,range(8)))
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ProductEmbedding).where(ProductEmbedding.product_id==pid))==1
    report['checks'].append({'concurrent_embedding_writers':8,'rows':1})
    print(json.dumps({'progress':'concurrent_embeddings_passed'}),flush=True)
    with TestClient(app,raise_server_exceptions=True) as client:
        def req(method,path,**kw):
            res=client.request(method,path,**kw)
            assert res.status_code==200,f'{path}:HTTP_{res.status_code}'
            return res.json()
        for gender in ['menswear','womenswear']:
            auth=req('POST','/auth/register',json={'email':f'styling-{uuid4().hex}@example.com','password':secrets.token_urlsafe(24)})
            headers={'Authorization':'Bearer '+auth['access_token']}
            # Simulate a device coming back with an expired access token.
            import jwt
            from app.api.deps import JWT_SECRET, JWT_ALG
            payload=jwt.decode(auth['access_token'],JWT_SECRET,algorithms=[JWT_ALG]);payload['exp']=int(time.time())-1
            expired=jwt.encode(payload,JWT_SECRET,algorithm=JWT_ALG)
            assert client.get('/users/me',headers={'Authorization':'Bearer '+expired}).status_code==401
            renewed=req('POST','/auth/refresh',json={'refresh_token':auth['refresh_token']})
            headers={'Authorization':'Bearer '+renewed['access_token']}
            assert req('GET','/users/me',headers=headers)['id']==payload['sub']
            report.setdefault('session_checks',[]).append({'gender':gender,'expired_access_recovered':True})
            req('PATCH','/onboarding/step1',headers=headers,json={'gender':'male' if gender=='menswear' else 'female','age_group':'25-34'})
            req('PATCH','/onboarding/step3',headers=headers,json={'style_preferences':['casual'],'price_segment':'mass'})
            req('PATCH','/fit-profile/me',headers=headers,json={'height':180,'gender_target':gender,'clothing_size':'XL','budget_min':0,'budget_max':30000})
            seen=set();total=0
            for page in range(4):
                started=time.monotonic()
                feed=req('GET','/feed?limit=30',headers=headers)
                assert len(feed)==30,f'{gender}:page{page}:count{len(feed)}'
                with SessionLocal() as db:
                    for item in feed:
                        p=item['product'];model=db.get(Product,p['id'])
                        assert product_is_adult(model) and product_is_fashion(model),f'child:{p["id"]}'
                        assert p['gender_target'] in (gender,'unisex'),f'wrong_gender:{p["id"]}'
                        keys=product_identity_keys(model);assert not keys&seen,f'repeat_page{page}'
                        seen.update(keys)
                        req('POST','/recommendations/events',headers=headers,json={'event_type':'view','product_id':p['id']})
                        action=['like','dislike','skip','save'][total%4]
                        req('POST','/recommendations/events',headers=headers,json={'event_type':action,'product_id':p['id']})
                        total+=1
                # Simultaneous refill/outfit reads used to insert the same embeddings.
                with ThreadPoolExecutor(max_workers=4) as pool:
                    concurrent=list(pool.map(lambda _:req('GET','/feed?limit=30',headers=headers),range(4)))
                assert all(len(items)==30 for items in concurrent), f'{gender}:page{page}:parallel_counts:{[len(items) for items in concurrent]}'
                with SessionLocal() as db:
                    for items in concurrent:
                        assert all(product_is_adult(db.get(Product,i['product']['id'])) and i['product']['gender_target'] in (gender,'unisex') for i in items)
                print(json.dumps({'progress':'mixed_swipes','gender':gender,'swipes':total,'parallel_feeds':4,'seconds':round(time.monotonic()-started,1)}),flush=True)
                # Fetch catalog complements, including for a card outside the next feed page.
                anchor_id=feed[0]['product']['id']
                related=req('GET',f'/products/{anchor_id}/complements',headers=headers)
                with SessionLocal() as db:
                    anchor=db.get(Product,anchor_id);roles=set()
                    for p in related:
                        other=db.get(Product,p['id'])
                        assert product_is_adult(other) and product_is_fashion(other) and p['gender_target'] in (gender,'unisex')
                        assert product_role(other) in COMPLEMENT_ROLES.get(product_role(anchor),())
                        assert pair_compatible(anchor,other)
                        roles.add(product_role(other))
                report['complements'].append({'gender':gender,'anchor':feed[0]['product']['title'],'roles':sorted(roles),'count':len(related)})
            fit=req('GET','/fit-profile/me',headers=headers)
            assert fit['gender_target']==gender and fit['clothing_size']=='XL'
            report['checks'].append({'gender':gender,'size':'XL','mixed_swipes':total,'view_events':total,'parallel_feed_requests':16})
            for scenario in ['daily','office','evening']:
                outfits=req('POST',f'/outfits/generate?count=3&scenario={scenario}',headers=headers)
                assert outfits,f'{gender}:{scenario}:empty_outfit'
                with SessionLocal() as db:
                    for outfit in outfits:
                        products=[db.get(Product,p['id']) for p in outfit['products'].values()]
                        roles={product_role(p) for p in products}
                        assert roles in ({'top','bottom','shoes'},{'one_piece','shoes'})
                        assert outfit_compatible(products)
                        assert all(product_is_adult(p) and scenario_product_ok(p,scenario) for p in products)
                        report['outfits'].append({'gender':gender,'scenario':scenario,'items':[{'title':p.title,'source':p.source,'role':product_role(p)} for p in products]})
                report['checks'].append({'gender':gender,'scenario':scenario,'complete_outfits':len(outfits)})
                print(json.dumps({'progress':'outfits','gender':gender,'scenario':scenario,'count':len(outfits)}),flush=True)
            filtered=req('GET','/feed?sizes=XL',headers=headers)
            assert filtered and all('XL' in p['product']['available_sizes'] for p in filtered)
            assert req('GET','/feed?max_price=1',headers=headers)==[]
            remaining=req('GET','/feed',headers=headers)
            assert remaining
            hidden=[p['product']['id'] for p in remaining]
            refill=req('GET','/feed',headers=headers,params=[('exclude_ids',pid) for pid in hidden])
            assert refill and not set(hidden)&{p['product']['id'] for p in refill}
            req('POST','/auth/logout',json={'refresh_token':auth['refresh_token']})
            assert client.post('/auth/refresh',json={'refresh_token':auth['refresh_token']}).status_code==401
            report['session_checks'][-1]['logout_revoked']=True
            report['session_checks'][-1]['inflight_swipes_excluded']=True
    report['result']='passed'
    print(json.dumps(report,ensure_ascii=False),flush=True)

if __name__=='__main__':
    try:main()
    except Exception as exc:
        print(json.dumps({'result':'failed','error':str(exc)}),flush=True)
        raise
