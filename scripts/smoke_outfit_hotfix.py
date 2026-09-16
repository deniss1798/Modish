"""HTTP and outfit quality checks. Only runs against the disposable restored DB."""
import json
import logging
import os
import secrets
import sys
import time
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.engine import make_url


def main():
    url = make_url(os.environ["DATABASE_URL"])
    assert url.host == "modish_outfit_test_pg" and url.database == "modish_test", "isolated_database_required"
    sys.path.insert(0, "/app")
    import dotenv
    dotenv.load_dotenv = lambda *args, **kwargs: False
    logging.disable(logging.CRITICAL)
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db import SessionLocal
    from app.models import Product
    from app.services.product_identity import product_identity_keys
    from app.services.outfit_quality import scenario_product_ok
    from app.services.catalog.rule_filters import product_gender_compatible

    with SessionLocal() as db:
        assert db.execute(text("select count(*) from products")).scalar_one() > 180000
    report = []
    with TestClient(app, raise_server_exceptions=False) as client:
        def request(method, route, **kwargs):
            response = client.request(method, route, **kwargs)
            assert response.status_code == 200, f"{method}:{route}:HTTP_{response.status_code}"
            return response.json()

        assert request("GET", "/health")["database"] == "ok"
        for gender in ["womenswear", "menswear"]:
            credentials = {"email": f"outfit-test-{uuid4().hex}@example.com", "password": secrets.token_urlsafe(24)}
            auth = request("POST", "/auth/register", json=credentials)
            headers = {"Authorization": "Bearer " + auth["access_token"]}
            request("PATCH", "/onboarding/step1", headers=headers, json={"gender":"female" if gender == "womenswear" else "male", "age_group":"25-34"})
            request("PATCH", "/onboarding/step3", headers=headers, json={"style_preferences":["casual"],"price_segment":"mass"})
            request("PATCH", "/fit-profile/me", headers=headers, json={"height":170, "gender_target":gender, "clothing_size":"M", "budget_min":0, "budget_max":10000})
            for scenario in ["daily", "office", "evening"]:
                start = time.monotonic()
                outfits = request("POST", f"/outfits/generate?count=3&scenario={scenario}", headers=headers)
                assert outfits, f"{gender}:{scenario}:empty"
                previous = []
                summary = []
                with SessionLocal() as db:
                    for outfit in outfits:
                        ids = list(outfit["items"].values())
                        products = list(db.execute(select(Product).where(Product.id.in_(ids))).scalars())
                        assert len(products) == len(ids) == len(outfit["products"])
                        assert set(outfit["items"]) in [{"top","bottom","shoes"},{"one_piece","shoes"}]
                        assert all(scenario_product_ok(p, scenario) for p in products)
                        assert all(product_gender_compatible(p, gender) for p in products)
                        keys = set().union(*(product_identity_keys(p) for p in products))
                        assert all(sum(bool(product_identity_keys(p) & old) for p in products) <= len(products)-2 for old in previous)
                        previous.append(keys)
                        summary.append([{key:p.get(key) for key in ("title","category","gender_target","image_url","color_family")} for p in outfit["products"].values()])
                listed = request("GET", f"/outfits?scenario={scenario}", headers=headers)
                assert listed, f"{gender}:{scenario}:list_empty"
                report.append({"gender":gender,"scenario":scenario,"outfits":len(outfits),"seconds":round(time.monotonic()-start,2),"items":summary})
                print(json.dumps(report[-1]),flush=True)
            feed = request("GET", "/feed?limit=30", headers=headers)
            assert feed, "empty_feed"
            with SessionLocal() as db:
                seen = set()
                for item in feed:
                    keys = product_identity_keys(db.get(Product,item["product"]["id"]))
                    assert not keys & seen, "duplicate_feed"
                    seen.update(keys)
                hidden = product_identity_keys(db.get(Product,feed[0]["product"]["id"]))
            request("POST","/recommendations/events",headers=headers,json={"event_type":"skip","product_id":feed[0]["product"]["id"]})
            next_feed = request("GET","/feed?limit=30",headers=headers)
            with SessionLocal() as db:
                assert all(not product_identity_keys(db.get(Product,item["product"]["id"])) & hidden for item in next_feed), "swiped_item_returned"
            wow = request("POST", "/onboarding/complete?count=3", headers=headers)
            assert wow["outfits"] and all(o["products"] and o["title"] != "daily" for o in wow["outfits"]), "wow_empty_or_untranslated"
    print(json.dumps({"result":"passed","scenarios":len(report),"checks":["health","full_catalog","outfits","diversity","gender","feed","skip_exclusion","wow"]}),flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"result":"failed","check":str(exc) if isinstance(exc,AssertionError) else type(exc).__name__}),flush=True)
        sys.exit(1)
