"""Device regressions tested only on an isolated restore of the real catalog."""
import os,sys,json,time,secrets,logging
from uuid import uuid4
from urllib.parse import urlsplit, unquote
from sqlalchemy import select,text
from sqlalchemy.engine import make_url

def main():
    url=make_url(os.environ['DATABASE_URL'])
    assert url.host=='modish_catalog_test_pg' and url.database=='modish_test','isolated_database_required'
    os.environ['RATE_LIMIT_PER_MINUTE']='100000'
    sys.path.insert(0,'/app')
    import dotenv
    dotenv.load_dotenv=lambda *a,**k:False
    logging.disable(logging.CRITICAL)
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db import SessionLocal
    from app.models import Product
    from app.services.product_identity import product_identity_keys
    from app.services.catalog_filters import CatalogFilters
    report={'checks':[], 'images':[], 'links':[]}
    with SessionLocal() as db:
        report['catalog_products']=db.execute(text('select count(*) from products')).scalar_one()
        assert report['catalog_products']>180000
    image_urls=set()
    with TestClient(app,raise_server_exceptions=True) as client:
        def req(method,path,**kw):
            res=client.request(method,path,**kw)
            assert res.status_code==200,f'{path}:HTTP_{res.status_code}'
            return res.json()
        for gender in ['menswear','womenswear']:
            start=time.monotonic()
            auth=req('POST','/auth/register',json={'email':f'device-{uuid4().hex}@example.com','password':secrets.token_urlsafe(24)})
            headers={'Authorization':'Bearer '+auth['access_token']}
            req('PATCH','/onboarding/step1',headers=headers,json={'gender':'male' if gender=='menswear' else 'female','age_group':'25-34'})
            req('PATCH','/onboarding/step3',headers=headers,json={'style_preferences':['casual'],'price_segment':'mass'})
            req('PATCH','/fit-profile/me',headers=headers,json={'height':180,'gender_target':gender,'clothing_size':'M','budget_min':0,'budget_max':20000})
            seen=set(); total=0; sources=set()
            for page in range(4):
                feed=req('GET','/feed?limit=30',headers=headers)
                assert len(feed)>=20,f'{gender}:page{page}:not_enough_products:{len(feed)}'
                with SessionLocal() as db:
                    for item in feed:
                        p=item['product']; assert p['gender_target'] in (gender,'unisex'),f'{gender}:wrong_gender:{p["id"]}'
                        raw=p.get('raw_params_json') or {}; marker=str(raw.get('param_пол','')).lower()
                        assert not (marker.startswith('жен') and gender=='menswear')
                        assert not (marker.startswith('муж') and gender=='womenswear')
                        dest=unquote(p['product_url']);assert '/zhenskaya/' not in dest if gender=='menswear' else '/muzhskaya/' not in dest
                        assert urlsplit(dest).scheme in ('https','http') and '/g/' not in urlsplit(dest).path,'affiliate_wrapper_leaked'
                        keys=product_identity_keys(db.get(Product,p['id']));assert not keys&seen,f'repeated_item_page{page}'
                        seen.update(keys);sources.add(p['source'])
                        if len(image_urls)<24:image_urls.add(p['image_url'])
                        if page==0 and len(report['links'])<8:
                            link=req('POST','/affiliate/click/'+p['id'],headers=headers)['url']
                            assert link==p['product_url'];report['links'].append(link)
                        req('POST','/recommendations/events',headers=headers,json={'event_type':'skip','product_id':p['id']})
                        total+=1
                fit=req('GET','/fit-profile/me',headers=headers)
                assert fit['gender_target']==gender,'profile_gender_changed_after_swipes'
                print(json.dumps({'progress':'swipes','gender':gender,'page':page+1,'total':total}),flush=True)
            report['checks'].append({'gender':gender,'swipes':total,'sources':sorted(sources),'seconds':round(time.monotonic()-start,2)})
            for params in [{'categories':'tops'},{'categories':'bottoms'},{'categories':'outerwear'},{'categories':'shoes'},{'colors':'Черный'},{'colors':'Белый'},{'sizes':'M'},{'sizes':'50'},{'min_price':2000,'max_price':10000},{'categories':'bottoms','colors':'Черный','min_price':1000,'max_price':20000}]:
                start=time.monotonic();feed=req('GET','/feed',headers=headers,params=params)
                assert feed,f'{gender}:empty_filter:{params}'
                f=CatalogFilters(params.get('min_price'),params.get('max_price'),tuple([params['categories']]) if 'categories' in params else (),tuple([params['sizes']]) if 'sizes' in params else (),tuple([params['colors']]) if 'colors' in params else ())
                with SessionLocal() as db:
                    assert all(f.matches(db.get(Product,item['product']['id'])) for item in feed),'filter_mismatch'
                print(json.dumps({'progress':'filter','gender':gender,'filter':params,'count':len(feed),'seconds':round(time.monotonic()-start,2)}),flush=True)
            assert req('GET','/feed?max_price=1',headers=headers)==[],'explicit_filter_relaxed'
            assert req('GET','/feed',headers=headers),'filter_reset_empty'
            report['checks'].append({'gender':gender,'filters':12})
            for scenario in ['daily','office','evening']:
                outfits=req('POST',f'/outfits/generate?count=3&scenario={scenario}',headers=headers)
                assert outfits,f'{gender}:{scenario}:empty_outfit'
                for outfit in outfits:
                    assert 'accessory' not in outfit['items']
                    for p in outfit['products'].values():
                        assert p['gender_target'] in (gender,'unisex'),f'{gender}:{scenario}:wrong_gender'
                        image_urls.add(p['image_url'])
                print(json.dumps({'progress':'outfit','gender':gender,'scenario':scenario,'count':len(outfits)}),flush=True)
                report['checks'].append({'gender':gender,'scenario':scenario,'outfits':len(outfits)})
        # Exercise the actual proxy route used after a direct phone download fails.
        for image_url in sorted(image_urls):
            res=client.get('/media/proxy-image',params={'url':image_url})
            report['images'].append({'url':image_url,'status':res.status_code,'bytes':len(res.content)})
        assert all(x['status']==200 and x['bytes']>100 for x in report['images']),'image_probe_failed'
    report['result']='passed'
    print(json.dumps(report,ensure_ascii=False),flush=True)

if __name__=='__main__':
    try:main()
    except Exception as e:
        print(json.dumps({'result':'failed','error':str(e)}),flush=True)
        raise
