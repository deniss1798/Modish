"""Bounded read-only size-field inventory on the exact isolated PostgreSQL DB.

Only aggregates are printed: no raw URLs, IDs, descriptions, credentials or
arbitrary source values. Reads retained observations without refreshing stock.
"""
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from sqlalchemy import select, text
from sqlalchemy.engine import make_url

try:
    url = make_url(os.environ["DATABASE_URL"])
    assert url.get_backend_name() == "postgresql", "isolated_postgresql_required"
    assert url.host == "modish_foundation_test_pg", "isolated_host_required"
    assert url.database == "modish_test", "isolated_database_required"
except Exception:
    print(json.dumps({"result": "failed", "error_type": "isolated_database_preflight_failed", "raw_error_omitted": True}))
    raise SystemExit(1) from None

from app.db import SessionLocal
from app.models import Product
from app.catalog_normalize import split_size_values
from app.services.catalog.size_resolver import explicit_size_system, resolve_product_sizes

SOURCES = ("baon", "shoppinglive", "serginnetti", "postmeridiem")
MAX_ROWS = 75000
SYSTEM_TOKEN = re.compile(r"RU|RUS|EU|EURO|UK|US|USA|INT|INTERNATIONAL|LETTER|LETTERS|WAIST", re.I)
SAFE_KEY = re.compile(r"[\w :\-]{1,96}")
DECIMAL_COMMA = re.compile(r"(?:(?:RU|EU|US|UK)\s*[:\-]?\s*)?\d{1,2},5", re.I)


def safe_key(key):
    value = str(key).strip().lower().replace(" ", "_")
    return value if SAFE_KEY.fullmatch(value) else "other_key"


def safe_system(value):
    token = str(value or "").strip()
    return token.upper() if SYSTEM_TOKEN.fullmatch(token) else "empty" if not token else "unrecognized_value"


def token_class(value):
    value = str(value or "").strip()
    if re.fullmatch(r"\d{1,2}", value):
        return "integer"
    if DECIMAL_COMMA.fullmatch(value):
        return "decimal_comma_half_size"
    if re.fullmatch(r"\d{1,2}\.5", value):
        return "decimal_point_half_size"
    if re.fullmatch(r"XXXS|XXS|XS|S|M|М|L|XL|XXL|XXXL|[2-6]XL", value, re.I):
        return "letter"
    if re.fullmatch(r"(?:RU|EU|US|UK)\s*[:\-]?\s*\d{1,2}(?:[,.]5)?", value, re.I):
        return "explicit_prefixed_numeric"
    if "/" in value or "-" in value or "," in value:
        return "compound_or_ambiguous"
    return "other"


def new_bucket():
    return {"products": Counter(), "reasons_by_size_token": Counter(),
        "failure_reasons_by_product": Counter(), "availability_by_size_token": Counter(),
        "explicit_system_by_product": Counter(), "raw_size_field_presence": Counter(),
        "original_size_param_names": Counter(), "original_size_token_classes": Counter(),
        "system_candidate_values": {}, "detail_key_presence": Counter(),
        "observed_oldest": None, "observed_newest": None}


def main():
    checked = datetime.now(timezone.utc)
    buckets = {}
    processed = 0
    with SessionLocal() as db:
        try:
            db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            db.execute(text("SET LOCAL statement_timeout = '60s'"))
            db.execute(text("SET LOCAL lock_timeout = '1s'"))
            query = select(Product).where(Product.source.in_(SOURCES), Product.is_active == 1).order_by(Product.id).limit(MAX_ROWS + 1)
            for product in db.scalars(query.execution_options(yield_per=200)):
                processed += 1
                assert processed <= MAX_ROWS, "bounded_inventory_limit_exceeded"
                category = product.category or "unknown"
                # Category labels are canonical field names, never arbitrary source text.
                category = category if re.fullmatch(r"[а-яё_]{1,48}", category) else "other_category"
                bucket = buckets.setdefault((product.source, category), new_bucket())
                bucket["products"]["active_stored"] += 1
                bucket["products"]["is_available"] += bool(product.is_available)
                seen = product.last_seen_in_feed_at
                if seen:
                    seen = seen.replace(tzinfo=timezone.utc) if seen.tzinfo is None else seen
                    stamp = seen.isoformat()
                    bucket["observed_oldest"] = min(bucket["observed_oldest"] or stamp, stamp)
                    bucket["observed_newest"] = max(bucket["observed_newest"] or stamp, stamp)
                    bucket["products"]["observation_within_72h"] += checked - timedelta(hours=72) <= seen <= checked
                resolved = resolve_product_sizes(product)
                bucket["products"]["has_known_available_size"] += any(s.known and s.availability == "available" for s in resolved)
                bucket["reasons_by_size_token"].update(s.reason or "known" for s in resolved)
                bucket["availability_by_size_token"].update(s.availability for s in resolved)
                bucket["failure_reasons_by_product"].update({s.reason for s in resolved if s.reason})
                bucket["products"]["missing_resolved_sizes"] += not bool(resolved)
                raw = product.feed_raw_json if isinstance(product.feed_raw_json, dict) else {}
                params = product.raw_params_json if isinstance(product.raw_params_json, dict) else {}
                bucket["explicit_system_by_product"][explicit_size_system(raw, params) or "unknown"] += 1
                for container_name, container in (("feed_raw", raw), ("raw_params", params)):
                    for key, value in container.items():
                        name = safe_key(key)
                        if "size" not in name and "размер" not in name:
                            continue
                        bucket["raw_size_field_presence"][container_name + ":" + name] += 1
                        if "unit" in name or "system" in name or "систем" in name:
                            values = value if isinstance(value, list) else [value]
                            bucket["system_candidate_values"].setdefault(container_name + ":" + name, Counter()).update(safe_system(v) for v in values)
                original_param = raw.get("param") or raw.get("Param") or ""
                if isinstance(original_param, str):
                    for part in original_param.split("|"):
                        if ":" not in part:
                            continue
                        name, value = (piece.strip() for piece in part.split(":", 1))
                        name = safe_key(name)
                        if "size" not in name and "размер" not in name:
                            continue
                        bucket["original_size_param_names"][name] += 1
                        if name in {"размер", "size"}:
                            bucket["original_size_token_classes"].update(token_class(v) for v in split_size_values(value))
                        elif "unit" in name or "system" in name or "систем" in name:
                            bucket["system_candidate_values"].setdefault("original_param:" + name, Counter()).update([safe_system(value)])
                if raw.get("available_sizes_detailed"):
                    bucket["products"]["incoming_available_sizes_detailed"] += 1
                for detail in product.available_sizes_detailed or []:
                    if isinstance(detail, dict):
                        bucket["detail_key_presence"].update(safe_key(key) for key in detail)
        finally:
            db.rollback()
    rows = []
    for (source, category), bucket in sorted(buckets.items()):
        rows.append({"source": source, "category": category,
            **{key: {candidate: dict(values) for candidate, values in value.items()} if key == "system_candidate_values"
                else dict(value) if isinstance(value, Counter) else value for key, value in bucket.items()}})
    print(json.dumps({"scope": "isolated_postgresql_stored_active_size_fields_read_only",
        "checked_at": checked.isoformat(), "max_rows": MAX_ROWS, "processed": processed,
        "read_only_transaction": True, "rollback_completed": True, "stock_timestamps_modified": False,
        "raw_urls_exported": False, "network_feed_requests": False, "buckets": rows,
        "limits": ["Stored active rows are dated observations; freshness is reported separately.",
            "System candidate unrecognized values are counted without printing raw values."]}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"result": "failed", "error_type": type(exc).__name__, "raw_error_omitted": True}))
        raise SystemExit(1) from None
