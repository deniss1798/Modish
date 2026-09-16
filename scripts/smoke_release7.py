"""Exercise successive and concurrent refreshes only on an isolated PG restore."""
import os, sys, json, secrets, logging
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from sqlalchemy import text
from sqlalchemy.engine import make_url

def main():
    url = make_url(os.environ['DATABASE_URL'])
    assert url.host == 'modish_release7_test_pg' and url.database == 'modish_test'
    os.environ['RATE_LIMIT_PER_MINUTE'] = '100000'
    sys.path.insert(0, '/app')
    import dotenv
    dotenv.load_dotenv = lambda *a, **k: False
    logging.disable(logging.CRITICAL)
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db import SessionLocal
    from app.models import Product
    from app.services.product_identity import product_identity_keys
    from app.services.catalog_scope import product_is_fashion
    from app.services.catalog_audience import product_is_adult
    from app.services.garment_roles import product_role
    from app.services.outfit_quality import outfit_compatible, scenario_product_ok
    report = {'checks': []}
    with SessionLocal() as db:
        report['catalog_products'] = db.scalar(text('select count(*) from products'))
        assert report['catalog_products'] > 180000
    with TestClient(app, raise_server_exceptions=True) as client:
        def req(method, path, **kw):
            res = client.request(method, path, **kw)
            assert res.status_code == 200, f'{path}:HTTP_{res.status_code}'
            return res.json()

        def verify(rows, scenario, gender, history):
            assert rows, f'{gender}:{scenario}:no_fresh_outfits'
            with SessionLocal() as db:
                keys_in_batch = []
                for row in rows:
                    products = [db.get(Product, p['id']) for p in row['products'].values()]
                    assert {product_role(p) for p in products} in ({'top', 'bottom', 'shoes'}, {'one_piece', 'shoes'})
                    assert outfit_compatible(products)
                    assert all(product_is_adult(p) and product_is_fashion(p) and scenario_product_ok(p, scenario) and p.gender_target in (gender, 'unisex') for p in products)
                    keys = set().union(*(product_identity_keys(p) for p in products))
                    assert all(sum(not bool(product_identity_keys(p) & old) for p in products) >= 2 for old in history)
                    assert all(not keys & other for other in keys_in_batch)
                    keys_in_batch.append(keys)
                history.extend(keys_in_batch)

        for gender in ['menswear', 'womenswear']:
            auth = req('POST', '/auth/register', json={'email': f'rotation-{uuid4().hex}@example.com', 'password': secrets.token_urlsafe(24)})
            headers = {'Authorization': 'Bearer ' + auth['access_token']}
            req('PATCH', '/onboarding/step1', headers=headers, json={'gender': 'male' if gender == 'menswear' else 'female', 'age_group': '25-34'})
            req('PATCH', '/onboarding/step3', headers=headers, json={'style_preferences': ['casual'], 'price_segment': 'mass'})
            req('PATCH', '/fit-profile/me', headers=headers, json={'height': 180, 'gender_target': gender, 'clothing_size': 'XL', 'budget_min': 0, 'budget_max': 30000})
            for scenario in ['daily', 'office', 'evening']:
                history = []; batches = []
                for refresh in range(3):
                    rows = req('POST', f'/outfits/generate?count=3&scenario={scenario}', headers=headers)
                    verify(rows, scenario, gender, history)
                    batches.append(tuple(sorted(tuple(sorted(row['items'].values())) for row in rows)))
                    listed = req('GET', f'/outfits?scenario={scenario}', headers=headers)
                    assert {r['id'] for r in listed} == {r['id'] for r in rows}
                    print(json.dumps({'progress': 'refresh', 'gender': gender, 'scenario': scenario, 'round': refresh + 1, 'count': len(rows)}), flush=True)
                assert len(set(batches)) == 3
                report['checks'].append({'gender': gender, 'scenario': scenario, 'refreshes': 3, 'distinct_batches': 3, 'counts': [len(batch) for batch in batches]})
            # Two simultaneous requests must observe each other's history.
            if gender == 'menswear':
                with ThreadPoolExecutor(max_workers=2) as pool:
                    responses = list(pool.map(lambda _: req('POST', '/outfits/generate?count=3&scenario=daily', headers=headers), range(2)))
                concurrent_history = []
                for rows in responses: verify(rows, 'daily', gender, concurrent_history)
                report['concurrent_refreshes'] = 2
    report['result'] = 'passed'
    print(json.dumps(report), flush=True)

if __name__ == '__main__':
    try: main()
    except Exception as exc:
        print(json.dumps({'result': 'failed', 'error': str(exc)}), flush=True)
        raise
