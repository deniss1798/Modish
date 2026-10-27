"""Pinned build16 four-source import into one new clone or fenced production.

The host verifies container/image identities, mounts /feeds and the pinned plan
read-only, and supplies a NEW private /outcomes directory. This worker does not
download, migrate, fence writers, retry a commit, or restore a database.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import sys
from urllib.parse import unquote, urlsplit
from uuid import UUID, uuid5

SCHEMA = "20261015_0028"
FEEDS = {"baon": "19982", "shoppinglive": "25461", "serginnetti": "26417", "postmeridiem": "25200"}
# Previous successful snapshot inputs, not cumulative retained Product rows.
PREVIOUS_ROWS = {"baon": 9318, "shoppinglive": 43402, "serginnetti": 17065, "postmeridiem": 378}
BASELINE_POSITIVE = {"baon": 7067, "shoppinglive": 36378, "serginnetti": 12403, "postmeridiem": 243}
FEED_ROOT, OUTCOME_ROOT = Path("/feeds"), Path("/outcomes")
MAX_BYTES, MAX_ROWS, WHOLE_DEADLINE_SECONDS = 256 * 1024 * 1024, 100000, 7200
IDENTITY_KEYS = ("id", "offer_id", "external_id", "product_id", "vendorcode", "vendor_code", "sku", "g_id")
PLAN_KEYS = {"schema_version", "release", "mode", "release_run_id", "download_run_id", "image_id",
    "archive_sha256", "manifest_sha256", "worker_sha256", "db", "schema"}
ALLOWED_REASONS = frozenset({
    "invalid_identity", "missing_merchant", "missing_title", "childrenswear", "unsupported_category",
    "unknown_gender", "missing_size", "unrecognized_size", "invalid_price", "unsupported_currency",
    "invalid_image_url", "invalid_product_url", "unknown_availability", "unknown_stock_observation_time",
    "stale_availability", "invalid_stock_observation_time", "out_of_stock", "missing_from_feed",
})
STATE = {"transaction": "not_started", "committed_sources": [], "rollback_completed": False,
    "needs_review": False, "ledger_started": False}
CURRENT_PLAN = None


def require(condition, code):
    if not condition:
        raise RuntimeError(code)


def uuid4_hex(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{32}", value) is None:
        return False
    parsed = UUID(value)
    return parsed.version == 4 and parsed.hex == value


def validate_plan_value(plan):
    require(isinstance(plan, dict) and set(plan) == PLAN_KEYS and type(plan["schema_version"]) is int
        and plan["schema_version"] == 1 and plan["release"] == "build16" and plan["schema"] == SCHEMA
        and isinstance(plan["mode"], str) and plan["mode"] in {"clone", "production"}, "exact_build16_plan_required")
    require(uuid4_hex(plan["release_run_id"]) and uuid4_hex(plan["download_run_id"]), "uuid4_plan_runs_required")
    require(isinstance(plan["image_id"], str) and re.fullmatch(r"sha256:[0-9a-f]{64}", plan["image_id"])
        and all(isinstance(plan[key], str) and re.fullmatch(r"[0-9a-f]{64}", plan[key])
            for key in ("archive_sha256", "manifest_sha256", "worker_sha256")), "pinned_plan_artifacts_required")
    run_id = plan["release_run_id"]
    expected = ({"host": "modish_foundation_test_pg", "database": "modish_refresh16_" + run_id,
        "role": "refresh16_" + run_id, "port": 5432} if plan["mode"] == "clone" else
        {"host": "modish_postgres", "database": "modish", "role": "modish_user", "port": 5432})
    require(isinstance(plan["db"], dict) and plan["db"] == expected and type(plan["db"]["port"]) is int,
        "mode_database_binding_required")
    return plan


def regular_bytes(path, bound):
    require(path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= bound,
        "bounded_regular_input_required")
    raw = path.read_bytes()
    require(0 < len(raw) <= bound, "bounded_regular_input_required")
    return raw


def approved_plan(plan_path, expected_sha):
    # No application module or database library may be imported before these pins.
    require(isinstance(expected_sha, str) and re.fullmatch(r"[0-9a-f]{64}", expected_sha), "pinned_plan_sha256_required")
    raw = regular_bytes(Path(plan_path), 65536)
    require(hashlib.sha256(raw).hexdigest() == expected_sha, "plan_hash_mismatch")
    plan = validate_plan_value(json.loads(raw))
    worker = regular_bytes(Path(__file__), 131072)
    require(hashlib.sha256(worker).hexdigest() == plan["worker_sha256"], "worker_self_hash_mismatch")
    return plan


def validate_database_url(value, plan):
    try:
        require(isinstance(value, str) and value and all(32 < ord(c) < 127 for c in value)
            and "\\" not in value, "exact_mode_database_url_required")
        url = urlsplit(value)
        expected = plan["db"]
        require(url.scheme in {"postgresql", "postgresql+psycopg"} and url.hostname == expected["host"]
            and url.port in (None, 5432) and unquote(url.username or "") == expected["role"]
            and bool(url.password) and url.path == "/" + expected["database"] and not url.query
            and not url.fragment, "exact_mode_database_url_required")
    except (TypeError, ValueError):
        raise RuntimeError("exact_mode_database_url_required") from None


def validate_environment(plan):
    require(sys.platform == "linux" and os.geteuid() == 0, "linux_root_worker_required")
    require(os.getenv("APP_ENV") == ("test" if plan["mode"] == "clone" else "production")
        and os.getenv("PYTHON_DOTENV_DISABLED") == "1", "mode_worker_environment_required")
    require(os.getenv("AUDIT_IMAGE_ID") == plan["image_id"]
        and os.getenv("AUDIT_ARCHIVE_SHA256") == plan["archive_sha256"], "runtime_artifact_bindings_required")
    require(not any(key.upper().startswith("PG") for key in os.environ)
        and "AUDIT_FIXED_NOW_UTC" not in os.environ, "connection_overrides_and_fixed_clock_forbidden")
    # Installed python-dotenv 1.0.1 ignores PYTHON_DOTENV_DISABLED.
    require(not Path("/app/.env").exists() and not Path("/app/.env").is_symlink(), "image_dotenv_file_must_be_absent")
    validate_database_url(os.getenv("DATABASE_URL"), plan)


def utc_stamp(value):
    require(isinstance(value, str) and 0 < len(value) <= 64, "utc_timestamp_required")
    stamp = datetime.fromisoformat(value)
    require(stamp.tzinfo is not None and stamp.utcoffset().total_seconds() == 0, "utc_timestamp_required")
    return stamp


def validate_source_counts(code, *, rows, distinct, previous_rows=0, previous_distinct=0,
        available_quality=None, baseline_available_quality=None):
    require(code in FEEDS and all(type(value) is int and value >= 0
        for value in (rows, distinct, previous_rows, previous_distinct))
        and 0 < rows <= MAX_ROWS and distinct == rows, "complete_distinct_source_identities_required")
    require(2 * rows >= max(PREVIOUS_ROWS[code], previous_rows)
        and 2 * distinct >= max(PREVIOUS_ROWS[code], previous_distinct), "unexpected_source_loss_requires_review")
    if available_quality is not None:
        require(type(available_quality) is int and available_quality >= 0
            and (baseline_available_quality is None or type(baseline_available_quality) is int
                and baseline_available_quality >= 0), "valid_quality_counts_required")
        baseline = max(BASELINE_POSITIVE[code], baseline_available_quality or 0)
        require(5 * available_quality >= 4 * baseline, "available_quality_loss_requires_review")


def verified_snapshots(plan):
    require(FEED_ROOT.is_dir() and not FEED_ROOT.is_symlink(), "feed_directory_required")
    manifest_bytes = regular_bytes(FEED_ROOT / "download-manifest.json", 16384)
    require(hashlib.sha256(manifest_bytes).hexdigest() == plan["manifest_sha256"], "pinned_download_manifest_required")
    manifest = json.loads(manifest_bytes)
    require(manifest.get("result") == "download_complete" and manifest.get("scope") ==
        "four_fixed_admitad_https_exports_no_database", "complete_download_manifest_required")
    require(manifest.get("observation_clock_basis") == "real_download_request_start_utc"
        and manifest.get("run_id") == plan["download_run_id"], "matching_actual_observation_required")
    entries = manifest.get("sources", [])
    require(isinstance(entries, list) and len(entries) == 4 and all(isinstance(entry, dict) for entry in entries)
        and {entry["code"] for entry in entries} == set(FEEDS), "exact_source_set_required")
    now, verified = datetime.now(timezone.utc), {}
    for entry in entries:
        code = entry["code"]
        require(entry["feed_id"] == FEEDS[code] and entry["filename"] == code + ".csv", "fixed_snapshot_identity")
        path = FEED_ROOT / entry["filename"]
        require(path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= MAX_BYTES,
            "snapshot_file_bound")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        require(type(entry["bytes"]) is int and path.stat().st_size == entry["bytes"]
            and digest.hexdigest() == entry["sha256"], "snapshot_hash_mismatch")
        observed, finished = utc_stamp(entry["observed_at"]), utc_stamp(entry["download_finished_at"])
        require(now - timedelta(hours=72) <= observed <= finished <= now + timedelta(minutes=5), "current_observation_bound")
        rows, identified, raw_available = 0, set(), 0
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.reader(stream, delimiter=";", strict=True)
            header = next(reader)
            keys = [value.strip().lower().replace(" ", "_") for value in header]
            require({"id", "name", "available", "price", "param"} <= set(keys)
                and bool({"currency", "currencyid"} & set(keys)) and len(set(keys)) == len(header),
                "expected_csv_header_required")
            for row in reader:
                if not row or not any(row):
                    continue
                require(len(row) == len(header), "malformed_csv_row")
                rows += 1
                require(rows <= MAX_ROWS, "snapshot_row_bound")
                values = dict(zip(keys, row))
                identity = next((values[key].strip() for key in IDENTITY_KEYS if values.get(key, "").strip()), "")
                require(identity and len(identity) <= 128 and identity not in identified,
                    "unique_valid_variant_identity_required")
                identified.add(identity)
                raw_available += values.get("available", "").strip().lower() in {"true", "1", "yes"}
        validate_source_counts(code, rows=rows, distinct=len(identified))
        verified[code] = {**entry, "rows": rows, "distinct_identified_ids": len(identified),
            "raw_available_rows": raw_available, "observed": observed, "path": path}
    return manifest, verified


def require_private_directory(path):
    require(path.is_dir() and not path.is_symlink(), "private_outcome_directory_required")
    info = path.stat()
    require(info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o700, "private_outcome_mode_required")


def durable_json(path, value):
    raw = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def deterministic_run_id(plan, code):
    require(code in FEEDS, "fixed_source_required")
    return str(uuid5(UUID(plan["release_run_id"]), "build16:" + plan["mode"] + ":" + code))


def plan_facts(plan, plan_sha):
    return {"release_run_id": plan["release_run_id"], "download_run_id": plan["download_run_id"],
        "mode": plan["mode"], "plan_sha256": plan_sha, "image_id": plan["image_id"],
        "archive_sha256": plan["archive_sha256"], "manifest_sha256": plan["manifest_sha256"],
        "worker_sha256": plan["worker_sha256"], "sync_run_ids": {code: deterministic_run_id(plan, code) for code in FEEDS}}


def start_ledger(plan, plan_sha):
    require_private_directory(OUTCOME_ROOT)
    require(not any(OUTCOME_ROOT.iterdir()), "existing_import_attempt_requires_review")
    durable_json(OUTCOME_ROOT / "started.json", {"operation": "build16_four_source_import",
        **plan_facts(plan, plan_sha), "started_at": datetime.now(timezone.utc).isoformat(),
        "automatic_retry_allowed": False})
    STATE["ledger_started"] = True


def identity_snapshot(db):
    from sqlalchemy import select
    from app.models import Product
    with db.execute(select(Product.id, Product.source, Product.source_id, Product.external_id)
            .where(Product.source.in_(tuple(FEEDS))).execution_options(yield_per=500)) as rows:
        return {row.id: (row.source, row.source_id, row.external_id) for row in rows}


def record_identity_snapshot(db):
    from sqlalchemy import select
    from app.models import CatalogRecord, ProductSource
    with db.execute(select(CatalogRecord.id, CatalogRecord.source_id, CatalogRecord.raw_key, CatalogRecord.external_id)
            .join(ProductSource, CatalogRecord.source_id == ProductSource.id)
            .where(ProductSource.code.in_(tuple(FEEDS))).execution_options(yield_per=500)) as rows:
        return {row.id: (row.source_id, row.raw_key, row.external_id) for row in rows}


def outside_state(db):
    """Hash full unaffected rows of every public table; emit no raw values."""
    from sqlalchemy import text
    targets = "('baon','shoppinglive','serginnetti','postmeridiem')"
    source_ids = "(SELECT id FROM public.product_sources WHERE code IN " + targets + ")"
    require(CURRENT_PLAN is not None, "validated_plan_required")
    new_runs = "(" + ",".join("'" + deterministic_run_id(CURRENT_PLAN, code) + "'" for code in FEEDS) + ")"
    predicates = {"products": "source IS NULL OR source NOT IN " + targets,
        "catalog_records": "source_id IS NULL OR source_id NOT IN " + source_ids,
        "product_sources": "code IS NULL OR code NOT IN " + targets,
        "catalog_sync_runs": "id NOT IN " + new_runs}
    tables = list(db.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")))
    require(len(tables) == 31 and len(set(tables)) == 31, "exact_31_public_tables_required")
    result = {}
    for table in tables:
        require(re.fullmatch(r"[a-z][a-z0-9_]{0,62}", table), "simple_public_table_names_required")
        query = ("SELECT count(*), md5(coalesce(string_agg(md5(row_to_json(r)::text), "
            "'' ORDER BY md5(row_to_json(r)::text)),'')) FROM public.\"" + table + '\" r WHERE ' + predicates.get(table, "TRUE"))
        result[table] = tuple(db.execute(text(query)).one())
    return result


def verify_physical_schema(db):
    from sqlalchemy import text
    require(db.scalar(text("SELECT version_num FROM alembic_version")) == SCHEMA, "physical_schema_0028_required")
    rows = list(db.execute(text("SELECT table_name,column_name,data_type,numeric_precision,numeric_scale,is_nullable,column_default "
        "FROM information_schema.columns WHERE table_schema='public' AND ((table_name='products' AND column_name IN ('price','old_price')) "
        "OR (table_name='outfits' AND column_name='total_price') OR (table_name='fit_profiles' AND column_name='gender_target') "
        "OR (table_name='users' AND column_name='recommendation_revision'))")))
    require(len(rows) == 5, "exact_migration_columns_required")
    for table, column, datatype, precision, scale, nullable, default in rows:
        if column in {"price", "old_price", "total_price"}:
            require((datatype, precision, scale) == ("numeric", 14, 2), "numeric_14_2_required")
        elif column == "gender_target":
            require(nullable == "YES" and default is None, "nullable_gender_without_default_required")
        else:
            require(datatype == "integer" and nullable == "NO" and default in {"0", "0::integer"},
                "recommendation_revision_default_required")


def source_contract(source):
    return tuple(getattr(source, key) for key in ("id", "code", "name", "network", "advertiser_id",
        "feed_url", "deeplink_template", "created_at"))


def bounded_reasons(values):
    output = {}
    for reason, count in values.items():
        key = reason if reason in ALLOWED_REASONS else "other_reason"
        output[key] = output.get(key, 0) + count
    return output


def unseen_ids_predicate(column, identifiers):
    from sqlalchemy import all_, bindparam, String
    from sqlalchemy.dialects.postgresql import ARRAY
    return column != all_(bindparam("seen_external_ids", value=list(identifiers), type_=ARRAY(String())))


def quality_count(db, source_id, code):
    from sqlalchemy import func, select
    from app.models import CatalogRecord, Product
    return int(db.scalar(select(func.count(func.distinct(Product.id))).join(CatalogRecord,
        (CatalogRecord.product_id == Product.id) & (CatalogRecord.source_id == Product.source_id)
        & (CatalogRecord.external_id == Product.external_id)).where(Product.source_id == source_id,
        Product.source == code, Product.is_available == 1, Product.is_active == 1,
        Product.is_deleted_from_feed == 0, CatalogRecord.status == "passed")) or 0)


def commit_once(db, report):
    durable_json(OUTCOME_ROOT / "commit-intent.json", {key: report[key] for key in
        ("release_run_id", "mode", "plan_sha256", "sync_run_ids")} | {
        "state": "commit_outcome_requires_review_until_confirmed", "automatic_retry_allowed": False})
    STATE["transaction"] = "commit_attempted"
    db.commit()
    # Update memory before any filesystem/report action may fail.
    STATE.update(transaction="committed", committed_sources=list(FEEDS), needs_review=False)


def rollback_failure(db):
    previous = STATE["transaction"]
    if previous == "committed":
        STATE["needs_review"] = True
        return
    try:
        db.rollback()
        STATE["rollback_completed"] = True
    except Exception:
        STATE["needs_review"] = True
    if previous == "commit_attempted":
        STATE.update(transaction="commit_outcome_uncertain", needs_review=True)
    elif STATE["rollback_completed"]:
        STATE["transaction"] = "rolled_back"
    else:
        STATE.update(transaction="rollback_unconfirmed", needs_review=True)


def deadline_handler(*_):
    raise RuntimeError("whole_worker_deadline_exceeded")


def main(argv=None):
    global CURRENT_PLAN
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--plan-sha256", required=True)
    args = parser.parse_args(argv)
    plan = approved_plan(args.plan, args.plan_sha256)
    CURRENT_PLAN = plan
    validate_environment(plan)
    signal.signal(signal.SIGALRM, deadline_handler)
    signal.setitimer(signal.ITIMER_REAL, WHOLE_DEADLINE_SECONDS)
    manifest, snapshots = verified_snapshots(plan)  # All four before any database connection.
    start_ledger(plan, args.plan_sha256)
    sys.path.insert(0, "/app")
    from sqlalchemy import create_engine, func, select, text, update
    from sqlalchemy.orm import sessionmaker
    from app.db import engine as application_engine
    validate_database_url(application_engine.url.render_as_string(hide_password=False), plan)
    from app.models import CatalogRecord, CatalogSyncRun, Product, ProductSource
    from app.integrations.admitad.feed_parser import parse_admitad_csv
    from app.services.catalog.catalog_records import store_record
    from app.services.catalog.feed_import_service import IngestResult, _finalize_run
    from app.services.catalog.versions import CANONICAL_VERSION, NORMALIZATION_VERSION, TAXONOMY_VERSION, ENRICHMENT_VERSION
    require((CANONICAL_VERSION, NORMALIZATION_VERSION, TAXONOMY_VERSION, ENRICHMENT_VERSION) ==
        ("canonical_v1", "catalog_rules_v7", "category_core_v1", "style_rules_v1"), "reviewed_catalog_pipeline_required")
    engine = create_engine(application_engine.url, future=True, pool_pre_ping=True, pool_size=1,
        max_overflow=0, pool_timeout=10, connect_args={"connect_timeout": 10,
            "options": "-c statement_timeout=120000 -c lock_timeout=5000 -c idle_in_transaction_session_timeout=180000"})
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    report = {"scope": "build16_four_source_" + plan["mode"] + "_import", **plan_facts(plan, args.plan_sha256),
        "started_at": datetime.now(timezone.utc).isoformat(), "sources": [], "result": "running",
        "stock_observation_basis": "actual_download_request_start_utc", "single_transaction": True,
        "schema": SCHEMA, "migration_executed": False, "automatic_retry_allowed": False}
    with session_factory() as db:
        try:
            db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
            STATE["transaction"] = "open"
            identity = tuple(db.execute(text("SELECT current_database(), current_user, inet_server_port()")).one())
            require(identity == (plan["db"]["database"], plan["db"]["role"], 5432), "actual_database_identity_mismatch")
            verify_physical_schema(db)
            db.execute(text("SELECT pg_advisory_xact_lock(20261009, 20)"))
            require(not db.scalar(select(func.count()).select_from(CatalogSyncRun).where(
                CatalogSyncRun.id.in_(tuple(report["sync_run_ids"].values())))), "existing_release_sync_runs_require_review")
            sources = {source.code: source for source in db.scalars(select(ProductSource).where(
                ProductSource.code.in_(tuple(FEEDS))).with_for_update())}
            require(set(sources) == set(FEEDS), "exact_existing_sources_required")
            contracts = {code: source_contract(source) for code, source in sources.items()}
            for code, source in sources.items():
                require(source.last_sync_at is None or source.last_sync_at < snapshots[code]["observed"],
                    "already_imported_or_newer_source_observation_requires_review")
                previous_rows = int(db.scalar(select(CatalogSyncRun.total_received).where(
                    CatalogSyncRun.source_id == source.id, CatalogSyncRun.status == "ok").order_by(
                        CatalogSyncRun.finished_at.desc().nullslast(), CatalogSyncRun.started_at.desc().nullslast(),
                        CatalogSyncRun.id.desc()).limit(1)) or 0)
                previous_distinct = int(db.scalar(select(func.count(func.distinct(CatalogRecord.external_id))).where(
                    CatalogRecord.source_id == source.id, CatalogRecord.external_id.is_not(None), CatalogRecord.status != "missing")) or 0)
                validate_source_counts(code, rows=snapshots[code]["rows"], distinct=snapshots[code]["distinct_identified_ids"],
                    previous_rows=previous_rows, previous_distinct=previous_distinct)
                snapshots[code].update(previous_rows=previous_rows, previous_distinct_identified_ids=previous_distinct,
                    previous_available_quality=quality_count(db, source.id, code))
            existing_ids, existing_record_ids = identity_snapshot(db), record_identity_snapshot(db)
            other_rows_before = outside_state(db)
            for code in FEEDS:
                source, entry = sources[code], snapshots[code]
                raw = regular_bytes(entry["path"], MAX_BYTES)
                require(hashlib.sha256(raw).hexdigest() == entry["sha256"], "snapshot_changed_before_parse")
                rows = parse_admitad_csv(raw.decode("utf-8-sig"))
                del raw
                require(len(rows) == entry["rows"], "csv_parser_row_mismatch")
                processed = datetime.now(timezone.utc)
                run = CatalogSyncRun(id=report["sync_run_ids"][code], source_id=source.id, status="running", started_at=processed)
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
                missing = db.execute(update(Product).where(Product.source_id == source.id, Product.source == code,
                    unseen_ids_predicate(Product.external_id, seen)).values(
                        is_deleted_from_feed=1, is_available=0, is_active=0, updated_at=processed))
                ingest.deactivated = missing.rowcount or 0
                db.execute(update(CatalogRecord).where(CatalogRecord.source_id == source.id,
                    CatalogRecord.external_id.is_not(None), unseen_ids_predicate(CatalogRecord.external_id, seen))
                    .values(status="missing", reasons=["missing_from_feed"]))
                ingest.ingested = ingest.created + ingest.updated
                _finalize_run(db, run=run, source=source, ingest=ingest, err=None, ok=True)
                db.flush()
                available_quality = quality_count(db, source.id, code)
                validate_source_counts(code, rows=entry["rows"], distinct=len(seen), previous_rows=entry["previous_rows"],
                    previous_distinct=entry["previous_distinct_identified_ids"], available_quality=available_quality,
                    baseline_available_quality=entry["previous_available_quality"])
                item = {"code": code, "sync_run_id": run.id, "rows": ingest.total_received,
                    "created": ingest.created, "updated": ingest.updated, "quarantined": ingest.skipped,
                    "missing": ingest.deactivated, "reasons": bounded_reasons(ingest.skip_breakdown),
                    "observed_at": entry["observed"].isoformat(), "distinct_identified_ids": len(seen),
                    "previous_rows": entry["previous_rows"], "previous_distinct_identified_ids": entry["previous_distinct_identified_ids"],
                    "raw_available_rows": entry["raw_available_rows"], "available_quality_rows": available_quality,
                    "previous_available_quality_rows": entry["previous_available_quality"],
                    "snapshot_sha256": entry["sha256"], "ordered_row_content_sha256": content_hash.hexdigest()}
                report["sources"].append(item)
                print(json.dumps({"progress": "source_prepared_not_committed", **item}), flush=True)
                del rows
            db.flush()
            current_ids, current_record_ids = identity_snapshot(db), record_identity_snapshot(db)
            require(all(current_ids.get(key) == value for key, value in existing_ids.items()), "existing_product_ids_changed")
            require(all(current_record_ids.get(key) == value for key, value in existing_record_ids.items()), "existing_record_ids_changed")
            require(all(source_contract(sources[code]) == value for code, value in contracts.items()), "source_configuration_changed")
            require(outside_state(db) == other_rows_before, "unaffected_rows_changed")
            require(all(datetime.now(timezone.utc) - timedelta(hours=72) <= entry["observed"]
                for entry in snapshots.values()), "observation_expired_before_commit")
            commit_once(db, report)
        except Exception:
            rollback_failure(db)
            raise
    report.update(result=plan["mode"] + "_import_committed", committed_sources=list(FEEDS),
        existing_product_ids_preserved=True, existing_record_ids_preserved=True,
        unaffected_rows_preserved=True, source_configuration_preserved=True,
        transaction_state="committed", needs_review=False, finished_at=datetime.now(timezone.utc).isoformat())
    durable_json(OUTCOME_ROOT / "committed.json", report)
    signal.setitimer(signal.ITIMER_REAL, 0)
    engine.dispose()
    print(json.dumps(report), flush=True)


def failure_report(error):
    if STATE["transaction"] == "committed":
        STATE["needs_review"] = True
    return {"result": "failed", "error_type": type(error).__name__, "raw_error_omitted": True,
        "release_run_id": CURRENT_PLAN["release_run_id"] if CURRENT_PLAN else None,
        "mode": CURRENT_PLAN["mode"] if CURRENT_PLAN else None,
        "committed_sources": STATE["committed_sources"], "transaction_state": STATE["transaction"],
        "rollback_completed": STATE["rollback_completed"], "needs_review": STATE["needs_review"],
        "automatic_retry_allowed": False, "check": str(error) if isinstance(error, RuntimeError)
            and re.fullmatch(r"[a-z][a-z0-9_]{0,100}", str(error)) else None}


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        if hasattr(signal, "setitimer"):
            signal.setitimer(signal.ITIMER_REAL, 0)
        failure = failure_report(error)
        if STATE["ledger_started"]:
            try:
                durable_json(OUTCOME_ROOT / "failed.json", failure)
            except Exception:
                pass  # Durable started/commit-intent still prohibit replay.
        print(json.dumps(failure), flush=True)
        raise SystemExit(1) from None
