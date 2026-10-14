"""Import complete fresh observations atomically into the exact isolated clone.

Download timestamps establish stock observation; processing never fabricates it.
No downloading, migration, broad stale deactivation or production DB connection.
"""
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from uuid import uuid4

sys.path.insert(0, "/app")
FEEDS = {"baon": "19982", "shoppinglive": "25461", "serginnetti": "26417", "postmeridiem": "25200"}
PREVIOUS_ROWS = {"baon": 8697, "shoppinglive": 41208, "serginnetti": 16975, "postmeridiem": 366}
TARGET = Path("/feeds")
MAX_BYTES = 256 * 1024 * 1024
MAX_ROWS_PER_SOURCE = 100000
IMPORT_STATE = {"transaction": "not_started", "committed_sources": [], "rollback_completed": False}
IDENTITY_KEYS = ("id", "offer_id", "external_id", "product_id", "vendorcode", "vendor_code", "sku", "g_id")
PG_CONNECTION_ENV = {"PGHOST", "PGHOSTADDR", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD",
    "PGSERVICE", "PGSERVICEFILE", "PGPASSFILE", "PGOPTIONS", "PGSSLMODE"}


def require(condition, code):
    if not condition:
        raise RuntimeError(code)


def utc_stamp(value):
    stamp = datetime.fromisoformat(value)
    require(stamp.tzinfo is not None and stamp.utcoffset().total_seconds() == 0, "utc_timestamp_required")
    return stamp


def unseen_ids_predicate(column, identifiers):
    # One PostgreSQL array parameter avoids the bind limit of an expanded
    # NOT IN list on a valid bounded feed with more than 65k distinct IDs.
    from sqlalchemy import all_, bindparam, String
    from sqlalchemy.dialects.postgresql import ARRAY
    return column != all_(bindparam("seen_external_ids", value=list(identifiers), type_=ARRAY(String())))


def verified_snapshots():
    require(TARGET.is_dir() and not TARGET.is_symlink(), "feed_directory_required")
    manifest = json.loads((TARGET / "download-manifest.json").read_text())
    require(manifest["result"] == "download_complete" and
        manifest["scope"] == "four_fixed_admitad_https_exports_no_database", "complete_download_manifest_required")
    require(manifest["observation_clock_basis"] == "real_download_request_start_utc", "actual_observation_required")
    require(manifest["run_id"] == os.environ["REFRESH_RUN_ID"], "matching_refresh_run_required")
    require(len(manifest["sources"]) == 4 and {r["code"] for r in manifest["sources"]} == set(FEEDS), "exact_source_set_required")
    now, verified = datetime.now(timezone.utc), {}
    for entry in manifest["sources"]:
        code = entry["code"]
        require(entry["feed_id"] == FEEDS[code] and entry["filename"] == code + ".csv", "fixed_snapshot_identity")
        path = TARGET / entry["filename"]
        require(path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= MAX_BYTES, "snapshot_file_bound")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        require(path.stat().st_size == entry["bytes"] and digest.hexdigest() == entry["sha256"], "snapshot_hash_mismatch")
        observed, finished = utc_stamp(entry["observed_at"]), utc_stamp(entry["download_finished_at"])
        require(now - timedelta(hours=72) <= observed <= finished <= now + timedelta(minutes=5), "current_observation_bound")
        rows, duplicate_rows, identified = 0, 0, {}
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.reader(stream, delimiter=";", strict=True)
            header = next(reader)
            key_order = [value.strip().lower().replace(" ", "_") for value in header]
            keys = set(key_order)
            require({"id", "name", "available", "price", "param"} <= keys and
                bool({"currency", "currencyid"} & keys), "expected_csv_header_required")
            require(len(keys) == len(header), "duplicate_csv_header")
            for row in reader:
                if not row or not any(row):
                    continue
                require(len(row) == len(header), "malformed_csv_row")
                rows += 1
                require(rows <= MAX_ROWS_PER_SOURCE, "snapshot_row_bound")
                values = dict(zip(key_order, row))
                identity = next((values[key].strip() for key in IDENTITY_KEYS if values.get(key, "").strip()), "")
                if identity and len(identity) <= 128:
                    row_hash = hashlib.sha256(json.dumps(row, ensure_ascii=False).encode()).hexdigest()
                    if identity in identified:
                        duplicate_rows += 1
                        require(identified[identity] == row_hash, "conflicting_duplicate_source_identity_requires_review")
                    else:
                        identified[identity] = row_hash
        require(rows >= PREVIOUS_ROWS[code] // 2, "unexpected_source_row_loss_requires_review")
        require(len(identified) >= PREVIOUS_ROWS[code] // 2, "unexpected_distinct_identity_loss_requires_review")
        verified[code] = {**entry, "rows": rows, "distinct_identified_ids": len(identified),
            "identical_duplicate_rows": duplicate_rows, "observed": observed, "path": path}
    return manifest, verified


def identity_snapshot(db):
    """Every old product ID stays attached to its source/external identity.

    Thin rows avoid materializing raw feeds/size evidence for an ID-only check.
    Keying by ID also preserves variants that share an external merchant ID.
    """
    from sqlalchemy import select
    from app.models import Product
    query = select(Product.id, Product.source, Product.external_id).where(
        Product.source.in_(tuple(FEEDS))).execution_options(yield_per=500)
    with db.execute(query) as rows:
        return {row.id: (row.source, row.external_id) for row in rows}


def main():
    from sqlalchemy.engine import make_url
    url = make_url(os.environ["DATABASE_URL"])
    require(url.get_backend_name() == "postgresql" and url.host == "modish_foundation_test_pg" and
        url.database == "modish_build14_test" and url.username == "modish_test" and url.port in (None, 5432) and not url.query, "exact_isolated_database_required")
    require(os.getenv("PYTHON_DOTENV_DISABLED") == "1" and os.getenv("APP_ENV") == "test" and
        not os.getenv("AUDIT_FIXED_NOW_UTC") and not PG_CONNECTION_ENV.intersection(os.environ), "safe_environment_required")
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", os.environ["AUDIT_IMAGE_ID"]), "immutable_image_required")
    require(re.fullmatch(r"[0-9a-f]{64}", os.environ["AUDIT_ARCHIVE_SHA256"]), "immutable_archive_required")
    manifest, snapshots = verified_snapshots()  # Validate all four before any DB write.
    from sqlalchemy import func, select, text, update
    from app.db import SessionLocal, engine
    require(engine.url.host == "modish_foundation_test_pg" and engine.url.database == "modish_build14_test" and engine.url.username == "modish_test"
        and engine.url.port in (None, 5432) and not engine.url.query, "actual_engine_url_mismatch")
    from app.models import CatalogRecord, CatalogSyncRun, Product, ProductSource
    from app.integrations.admitad.feed_parser import parse_admitad_csv
    from app.services.catalog.catalog_records import store_record
    from app.services.catalog.feed_import_service import IngestResult, _finalize_run
    report = {"scope": "build14_four_fixed_snapshots_atomic_isolated_clone_only", "run_id": manifest["run_id"],
        "image_id": os.environ["AUDIT_IMAGE_ID"], "archive_sha256": os.environ["AUDIT_ARCHIVE_SHA256"],
        "started_at": datetime.now(timezone.utc).isoformat(), "sources": [], "result": "running",
        "production_database_connected": False, "stock_observation_basis": "actual_download_request_start_utc",
        "raw_timestamps_injected": False, "other_sources_modified": False, "single_transaction": True}
    with SessionLocal() as db:
        try:
            db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
            IMPORT_STATE["transaction"] = "open"
            db.execute(text("SET LOCAL statement_timeout = '120s'"))
            db.execute(text("SET LOCAL lock_timeout = '5s'"))
            identity = db.execute(text("SELECT current_database(), current_user, inet_server_port()")).one()
            require(tuple(identity) == ("modish_build14_test", "modish_test", 5432), "actual_database_identity_mismatch")
            require(db.scalar(text("SELECT version_num FROM alembic_version")) == "20261013_0025", "test_revision_required")
            db.execute(text("SELECT pg_advisory_xact_lock(20261009, 20)"))
            sources = {s.code: s for s in db.scalars(select(ProductSource).where(ProductSource.code.in_(tuple(FEEDS))).with_for_update())}
            require(set(sources) == set(FEEDS), "exact_existing_sources_required")
            for code, source in sources.items():
                previous_distinct = db.scalar(select(func.count(func.distinct(CatalogRecord.external_id))).where(
                    CatalogRecord.source_id == source.id, CatalogRecord.external_id.is_not(None), CatalogRecord.status != "missing"))
                required_distinct = max(PREVIOUS_ROWS[code] // 2, int(previous_distinct or 0) // 2)
                require(snapshots[code]["distinct_identified_ids"] >= required_distinct,
                    "unexpected_distinct_identity_loss_from_clone_requires_review")
                snapshots[code]["previous_distinct_identified_ids"] = int(previous_distinct or 0)
            existing_ids = identity_snapshot(db)
            for code in FEEDS:
                source, entry = sources[code], snapshots[code]
                rows = parse_admitad_csv(entry["path"].read_text(encoding="utf-8-sig"))
                require(len(rows) == entry["rows"], "csv_parser_row_mismatch")
                processed = datetime.now(timezone.utc)
                run = CatalogSyncRun(id=str(uuid4()), source_id=source.id, status="running", started_at=processed)
                db.add(run)
                db.flush()
                ingest = IngestResult(total_received=len(rows), sync_ts=entry["observed"])
                seen, content_hash = set(), hashlib.sha256()
                for row in rows:
                    record, action = store_record(db, source, row, processed, last_seen_at=entry["observed"])
                    expected = hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                    require(record.raw_hash == expected, "deterministic_raw_fingerprint_mismatch")
                    content_hash.update(bytes.fromhex(expected))
                    if record.external_id:
                        seen.add(record.external_id)
                    if action == "created":
                        ingest.created += 1
                    elif action == "updated":
                        ingest.updated += 1
                    else:
                        ingest.skipped += 1
                        for reason in record.reasons:
                            ingest.skip_breakdown[reason] = ingest.skip_breakdown.get(reason, 0) + 1
                require(len(seen) == entry["distinct_identified_ids"], "parsed_distinct_identity_mismatch")
                missing = db.execute(update(Product).where(Product.source_id == source.id,
                    Product.source == code, unseen_ids_predicate(Product.external_id, seen)).values(
                        is_deleted_from_feed=1, is_available=0, is_active=0, updated_at=processed))
                ingest.deactivated = missing.rowcount or 0
                db.execute(update(CatalogRecord).where(CatalogRecord.source_id == source.id,
                    CatalogRecord.external_id.is_not(None), unseen_ids_predicate(CatalogRecord.external_id, seen))
                    .values(status="missing", reasons=["missing_from_feed"]))
                ingest.ingested = ingest.created + ingest.updated
                _finalize_run(db, run=run, source=source, ingest=ingest, err=None, ok=True)
                item = {"code": code, "rows": ingest.total_received, "created": ingest.created,
                    "updated": ingest.updated, "quarantined": ingest.skipped, "missing": ingest.deactivated,
                    "reasons": ingest.skip_breakdown, "observed_at": entry["observed"].isoformat(),
                    "distinct_identified_ids": len(seen), "identical_duplicate_rows": entry["identical_duplicate_rows"],
                    "previous_distinct_identified_ids": entry["previous_distinct_identified_ids"],
                    "snapshot_sha256": entry["sha256"], "ordered_row_content_sha256": content_hash.hexdigest()}
                report["sources"].append(item)
                print(json.dumps({"progress": "source_prepared_not_committed", **item}), file=sys.stderr, flush=True)
                del rows
            current_ids = identity_snapshot(db)
            require(all(current_ids.get(pid) == identity for pid, identity in existing_ids.items()), "existing_product_ids_changed")
            IMPORT_STATE["transaction"] = "commit_attempted"
            db.commit()
            IMPORT_STATE.update(transaction="committed", committed_sources=list(FEEDS))
            report.update(result="import_committed", committed_sources=list(FEEDS), existing_product_ids_preserved=True,
                finished_at=datetime.now(timezone.utc).isoformat())
        except Exception:
            db.rollback()
            IMPORT_STATE["rollback_completed"] = True
            if IMPORT_STATE["transaction"] != "commit_attempted":
                IMPORT_STATE["transaction"] = "rolled_back"
            else:
                IMPORT_STATE["transaction"] = "commit_outcome_uncertain"
            raise
    print(json.dumps(report))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"result": "failed", "error_type": type(error).__name__, "raw_error_omitted": True,
            "committed_sources": IMPORT_STATE["committed_sources"], "transaction_state": IMPORT_STATE["transaction"],
            "rollback_completed": IMPORT_STATE["rollback_completed"],
            "check": str(error) if isinstance(error, RuntimeError) and re.fullmatch(r"[a-z][a-z0-9_]{0,100}", str(error)) else None}))
        raise SystemExit(1) from None
