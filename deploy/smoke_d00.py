"""Run ONLY inside an isolated restored PostgreSQL environment; never production.

Uses in-process HTTP requests, no external feeds/AI/outbound URLs. Creates a
synthetic user and one product in the disposable database. Output is redacted.
"""
import json
import logging
import math
import os
import secrets
import sys
import time
from uuid import uuid4


def main():
    from sqlalchemy import text
    from sqlalchemy.engine import make_url

    url = make_url(os.environ["DATABASE_URL"])
    if url.host != "modish-d00-postgres" or url.database != "modish_restore_d00":
        raise RuntimeError("isolated_database_required")
    if os.environ.get("MODISH_ISOLATED_SMOKE") != "1":
        raise RuntimeError("explicit_isolated_smoke_required")
    sys.path.insert(0, "/app")
    import dotenv
    dotenv.load_dotenv = lambda *args, **kwargs: False
    logging.disable(logging.CRITICAL)
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db import SessionLocal
    from app.models import Product

    checks = []
    started = time.monotonic()
    with SessionLocal() as db:
        if db.execute(text("SELECT current_database()")).scalar_one() != "modish_restore_d00":
            raise RuntimeError("wrong_database")
        revision = db.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        if revision != "20260824_0019":
            raise RuntimeError("unexpected_schema_revision")
        checks.append("schema")

    run_id = uuid4().hex
    product_id = str(uuid4())
    source = "smoke_" + run_id
    credentials = {"email": f"smoke-{run_id}@example.com", "password": secrets.token_urlsafe(24)}
    with TestClient(app, raise_server_exceptions=False) as client:
        def request(label, method, route, expected=200, **kwargs):
            response = client.request(method, route, **kwargs)
            if response.status_code != expected:
                raise RuntimeError(f"{label}:HTTP_{response.status_code}")
            checks.append(label)
            return response.json()

        health = request("health", "GET", "/health")
        if health.get("database") != "ok" or health.get("status") != "ok":
            raise RuntimeError("database_not_ready")
        request("unauthorized_feed", "GET", "/feed", expected=401)
        registered = request("register", "POST", "/auth/register", json=credentials)
        request("duplicate_register", "POST", "/auth/register", expected=409, json=credentials)
        logged = request("login", "POST", "/auth/login", json=credentials)
        if logged["user"]["id"] != registered["user"]["id"]:
            raise RuntimeError("wrong_login_identity")
        headers = {"Authorization": "Bearer " + logged["access_token"]}
        request("fit_profile", "PATCH", "/fit-profile/me", headers=headers, json={
            "height": 170, "gender_target": "womenswear", "clothing_size": "M",
            "budget_min": 0, "budget_max": 10000,
        })
        with SessionLocal() as db:
            db.add(Product(
                id=product_id, external_id=run_id, source=source,
                title="Smoke white shirt", brand="Smoke", category="shirt",
                price=1990, currency="RUB", image_url="https://example.invalid/image.jpg",
                product_url="https://example.invalid/product",
                affiliate_url="https://example.invalid/product",
                available_sizes=["M"], colors=["white"], gender_target="womenswear",
                style_tags=["minimalism"], is_available=1, is_active=1, is_deleted_from_feed=0,
            ))
            db.commit()
        products = request("catalog", "GET", f"/products?source={source}&limit=3")
        if not any(item["id"] == product_id for item in products):
            raise RuntimeError("fixture_missing_from_catalog")
        product = request("detail", "GET", "/products/" + product_id)
        if product["price"] != 1990 or product["currency"] != "RUB":
            raise RuntimeError("product_contract_failed")
        feed = request("feed", "GET", f"/feed?source={source}&limit=3&scenario=daily", headers=headers)
        if len(feed) != 1 or feed[0]["product"]["id"] != product_id:
            raise RuntimeError("fixture_missing_from_feed")
        if not math.isfinite(feed[0]["final_score"]):
            raise RuntimeError("nonfinite_score")
        catalog_feed = request("restored_catalog_feed", "GET", "/feed?limit=3&scenario=daily", headers=headers)
        if not catalog_feed:
            raise RuntimeError("restored_catalog_feed_empty")
        request("save", "POST", "/recommendations/events", headers=headers,
                json={"event_type": "save", "product_id": product_id})
        saved = request("saved_list", "GET", "/saved-products", headers=headers)
        if not any(item["product"]["id"] == product_id for item in saved):
            raise RuntimeError("saved_product_missing")
        request("unsave", "POST", "/recommendations/events", headers=headers,
                json={"event_type": "unsave", "product_id": product_id})
        saved = request("unsaved_list", "GET", "/saved-products", headers=headers)
        if any(item["product"]["id"] == product_id for item in saved):
            raise RuntimeError("unsaved_product_present")
    print(json.dumps({"result": "passed", "checks": checks, "seconds": round(time.monotonic()-started, 2)}))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Never emit response bodies, tokens, SQL parameters or vendor errors.
        label = str(error) if type(error) is RuntimeError else type(error).__name__
        print(json.dumps({"result": "failed", "check": label}))
        raise SystemExit(1) from None
