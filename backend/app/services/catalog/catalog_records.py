"""Persist rejected rows too; replay rules without downloading merchant feeds."""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from uuid import uuid4

from sqlalchemy import func, select

from ...models import CatalogRecord, Product, ProductSource
from ...money import json_rubles
from .affiliate_link_service import apply_deeplink_for_product
from .merchant_adapters import get_adapter
from .product_normalizer import orm_kwargs_from_normalized
from .quality_gate import QualityDecision, evaluate_product
from .product_dna import build_product_dna
from .versions import CANONICAL_VERSION, NORMALIZATION_VERSION, TAXONOMY_VERSION, ENRICHMENT_VERSION


def prepare_record(row, source, timestamp, *, observed_at=None):
    raw = deepcopy(row) if isinstance(row, dict) else {"invalid_row": deepcopy(row)}
    digest = hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    adapter = get_adapter(source.code)
    normalized = adapter.normalize(row, source)
    if normalized is None:
        return raw, digest, None, None, QualityDecision("quarantined", ("invalid_identity",))
    observed_at = observed_at or timestamp
    payload = orm_kwargs_from_normalized(normalized, source_id=source.id, sync_ts=observed_at)
    original = normalized.original_url or ""
    affiliate = apply_deeplink_for_product(normalized.affiliate_url or original, source,
        external_id=normalized.external_id, original_url=original)
    payload.update(affiliate_url=affiliate or None, original_url=original or None,
                   product_url=affiliate or original)
    decision = evaluate_product(Product(**payload), checked_at=timestamp)
    payload["is_active"] = int(decision.status == "passed")
    payload["is_available"] = int(normalized.availability_status == "in_stock")
    # Snapshot contains only canonical facts, not repeated raw records or DB state.
    canonical = {k: v for k, v in payload.items() if k not in {
        "feed_raw_json", "raw_params_json", "source_id", "last_seen_in_feed_at",
        "is_active", "is_available", "is_deleted_from_feed",
    }}
    canonical.update(schema_version=CANONICAL_VERSION, taxonomy_version=TAXONOMY_VERSION,
        enrichment_version=ENRICHMENT_VERSION, adapter=adapter.code,
        delivery_eligibility="unknown", availability_observed_at=observed_at.isoformat())
    canonical['price'] = json_rubles(payload['price'])
    canonical['old_price'] = json_rubles(payload['old_price']) if payload['old_price'] is not None else None
    # Versioned semantic evidence is stored beside canonical facts. It cannot
    # upgrade an unknown size/stock state or alter the quality decision above.
    canonical["product_dna"] = build_product_dna(Product(**payload))
    return raw, digest, payload, canonical, decision


def store_record(db, source, row, timestamp, *, last_seen_at=None):
    raw, digest, payload, canonical, decision = prepare_record(row, source, timestamp, observed_at=last_seen_at)
    external_id = payload["external_id"] if payload else None
    key = "id:" + external_id if external_id else "sha256:" + digest
    record = db.scalar(select(CatalogRecord).where(CatalogRecord.source_id == source.id, CatalogRecord.raw_key == key))
    if record is None:
        record = CatalogRecord(id=str(uuid4()), source_id=source.id, raw_key=key, first_seen_at=timestamp)
        db.add(record)
    record.raw_json, record.raw_hash = raw, digest
    record.external_id, record.canonical_json = external_id, canonical
    record.status, record.reasons = decision.status, list(decision.reasons)
    record.normalization_version = NORMALIZATION_VERSION
    record.last_seen_at = last_seen_at or timestamp
    record.processed_at = timestamp
    product = db.scalar(select(Product).where(Product.source == source.code, Product.external_id == external_id)) if external_id else None
    action = "quarantined"
    if payload and decision.status != "quarantined":
        payload["last_seen_in_feed_at"] = record.last_seen_at
        if product is None:
            product = Product(id=str(uuid4()), **payload)
            db.add(product)
            action = "created"
        else:
            for name, value in payload.items():
                setattr(product, name, value)
            action = "updated"
        product.updated_at = timestamp
    elif product is not None:
        # Preserve the last valid facts, but stop serving the stale product.
        product.is_active = 0
        product.is_available = 0
        product.is_deleted_from_feed = 0
        product.last_seen_in_feed_at = record.last_seen_at
        product.updated_at = timestamp
    if product is not None:
        db.flush([product])
        record.product_id = product.id
    db.flush([record])
    return record, action


def quality_summary(db, source_id=None):
    query = select(CatalogRecord.source_id, CatalogRecord.status, func.count()).group_by(CatalogRecord.source_id, CatalogRecord.status)
    reasons_query = select(CatalogRecord.reasons).where(CatalogRecord.status == "quarantined")
    if source_id:
        query = query.where(CatalogRecord.source_id == source_id)
        reasons_query = reasons_query.where(CatalogRecord.source_id == source_id)
    counts = Counter()
    sources = {}
    for sid, status, count in db.execute(query):
        counts[status] += count
        sources.setdefault(sid, {})[status] = count
    reasons = Counter()
    for values in db.scalars(reasons_query.execution_options(yield_per=500)):
        reasons.update(values or [])
    total = sum(counts.values())
    return {"normalization_version": NORMALIZATION_VERSION, "total_records": total,
        "counts": dict(counts), "pass_rate": counts["passed"] / total if total else None,
        "quarantine_rate": counts["quarantined"] / total if total else None,
        "reasons": dict(reasons), "by_source": sources,
        "scope": "stored_records_only", "network_urls_verified": False}


def reprocess_records(db, source, *, dry_run=True, limit=200, after_id=None):
    db.execute(select(ProductSource.id).where(ProductSource.id == source.id).with_for_update()).scalar_one()
    query = select(CatalogRecord).where(CatalogRecord.source_id == source.id, CatalogRecord.status != "missing")
    if after_id:
        query = query.where(CatalogRecord.id > after_id)
    records = list(db.scalars(query.order_by(CatalogRecord.id).limit(limit + 1)))
    has_more = len(records) > limit
    records = records[:limit]
    counts, reasons = Counter(), Counter()
    now = datetime.now(timezone.utc)
    for record in records:
        if dry_run:
            decision = prepare_record(record.raw_json, source, now, observed_at=record.last_seen_at)[-1]
            counts[decision.status] += 1
            reasons.update(decision.reasons)
        else:
            updated, _ = store_record(db, source, record.raw_json, now, last_seen_at=record.last_seen_at)
            counts[updated.status] += 1
            reasons.update(updated.reasons)
    return {"dry_run": dry_run, "processed": len(records), "counts": dict(counts),
        "reasons": dict(reasons), "has_more": has_more,
        "next_after_id": records[-1].id if has_more else None,
        "normalization_version": NORMALIZATION_VERSION}
