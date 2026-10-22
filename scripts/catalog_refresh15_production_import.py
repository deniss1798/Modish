"""One approved build15 import of four rehearsed snapshots into production.

This is a separate production-only worker, not a switch that weakens clone guards.
The host must fence writers, verify the pinned image/containers and final backup,
migrate to 0028, and mount /feeds and /plan read-only plus a NEW private /outcome.
No downloading, migrations, retries, automatic restore, API startup or auth here.
An approval environment value is an execution guard, not human authorization.
"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from urllib.parse import unquote, urlsplit
from uuid import UUID, uuid4

RUN_ID = "9c4c8c7fe5a045068cde76d81df12104"
ARCHIVE = "5184f378245db0073154369f1b6e083c8d584cb9006788993cd4727866e26c24"
IMAGE = "sha256:633bce227d3ce3277137c77f32f2c6e83bb338d91784803df424d7592a464aec"
MANIFEST = "5d2dde18bcb09cde948d044bc57d2178fd035aebc0698a0996d79dd5ab4ce23c"
SOURCE_REVISION, TARGET_REVISION = "20261013_0025", "20261015_0028"
APPROVAL = "APPROVE_BUILD15_AND_FOUR_SOURCE_PRODUCTION_IMPORT"
WORKER_JWT = "unused-build15-catalog-import-worker-no-auth"
FEEDS = {"baon": "19982", "shoppinglive": "25461", "serginnetti": "26417", "postmeridiem": "25200"}
PREVIOUS_ROWS = {"baon": 8891, "shoppinglive": 41592, "serginnetti": 17056, "postmeridiem": 371}
REHEARSED_ROWS = {"baon": 9318, "shoppinglive": 43402, "serginnetti": 17065, "postmeridiem": 378}
PRODUCTION_DATABASE = {"host": "postgres", "port": 5432, "name": "modish", "role": "modish_user"}
PRODUCTION_CONTAINERS = {
    "backend": {"name": "modish_backend", "id": "7658a4e5e4cacc252630658fbe71d8bb10298a5769cb8958271c6f6482b9e950",
        "image": "sha256:eeea30209dcaa366fd936b6181ce84d47fc475a342df257893d58bb725dac1ec"},
    "postgres": {"name": "modish_postgres", "id": "987af124c33aa2984c6d4379417036f4278f3024ba75c6f145dcd29838da667e",
        "image": "sha256:5d143123fdf80462d1778cd4f24b9f7ca13c87174bca19141fb194c5a1ebca59"},
    "redis": {"name": "modish_redis", "id": "7d097417acdcb98861c1b4f1df83fcf562db12c231d6f2f44f25180f7308e615",
        "image": "sha256:3e1b24a1a8f24ff926b15e5ace8c38a03e5657fb66e1fc7e5188e315aa5fa094"},
}
PLAN_PATH, FEED_ROOT, OUTCOME_ROOT = Path("/plan/activation-plan.json"), Path("/feeds"), Path("/outcome")
MAX_BYTES, MAX_ROWS = 256 * 1024 * 1024, 100000
IDENTITY_KEYS = ("id", "offer_id", "external_id", "product_id", "vendorcode", "vendor_code", "sku", "g_id")
DISABLED_FLAGS = ("ADMITAD_ENABLED", "STYLE_VISUAL_ENABLED", "CLOSED_ALPHA_ENABLED", "ALLOW_DEV_ANALYZE")
PRIVATE_ENV = {"AI_API_KEY", "OPENAI_API_KEY", "ADMITAD_WEBSITE_ID", "ADMITAD_EXPORT_USER", "ADMITAD_EXPORT_CODE"}
ALLOWED_REASONS = frozenset({
    "invalid_identity", "missing_merchant", "missing_title", "childrenswear", "unsupported_category",
    "unknown_gender", "missing_size", "unrecognized_size", "invalid_price", "unsupported_currency",
    "invalid_image_url", "invalid_product_url", "unknown_availability", "unknown_stock_observation_time",
    "stale_availability", "invalid_stock_observation_time", "out_of_stock", "missing_from_feed",
})
STATE = {"transaction": "not_started", "committed_sources": [], "rollback_completed": False,
    "needs_review": False, "ledger_started": False}


def require(condition, code):
    if not condition:
        raise RuntimeError(code)


def utc_stamp(value):
    stamp = datetime.fromisoformat(value)
    require(stamp.tzinfo is not None and stamp.utcoffset().total_seconds() == 0, "utc_timestamp_required")
    return stamp


def regular_bytes(path, bound):
    require(path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= bound,
        "bounded_regular_input_required")
    return path.read_bytes()


def validate_plan_value(plan):
    required = {"operation": "build15_production_activation_plan", "run_id": RUN_ID,
        "archive_sha256": ARCHIVE, "image_id": IMAGE, "snapshot_manifest_sha256": MANIFEST,
        "source_revision": SOURCE_REVISION, "target_revision": TARGET_REVISION,
        "approval_token": APPROVAL, "approval_required": True}
    require(isinstance(plan, dict) and all(plan.get(k) == v for k, v in required.items())
        and plan.get("approval_required") is True, "exact_reviewed_activation_plan_required")
    value = plan.get("plan_id", "")
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value) is not None,
        "new_uuid4_plan_id_required")
    parsed = UUID(value)
    require(parsed.version == 4 and parsed.hex == value and value != RUN_ID, "new_uuid4_plan_id_required")
    require(isinstance(plan.get("production_database"), dict) and all(
        plan["production_database"].get(k) == v for k, v in PRODUCTION_DATABASE.items()),
        "exact_production_database_plan_required")
    require(isinstance(plan.get("feeds"), dict) and plan["feeds"] == FEEDS, "exact_four_planned_feeds_required")
    containers = plan.get("production_containers", {})
    require(isinstance(containers, dict) and all(isinstance(containers.get(name), dict)
        and all(containers[name].get(k) == v for k, v in expected.items())
        for name, expected in PRODUCTION_CONTAINERS.items()), "exact_production_container_plan_required")
    return plan


def approved_plan():
    # This must precede importing any application/database modules.
    require(os.environ.get("PRODUCTION_APPROVAL_TOKEN") == APPROVAL, "explicit_production_approval_required")
    expected = os.environ.get("PLAN_SHA256", "")
    require(re.fullmatch(r"[0-9a-f]{64}", expected) is not None, "pinned_activation_plan_required")
    raw = regular_bytes(PLAN_PATH, 65536)
    require(hashlib.sha256(raw).hexdigest() == expected, "activation_plan_hash_mismatch")
    return validate_plan_value(json.loads(raw))


def validate_database_url(value):
    try:
        require(isinstance(value, str) and value and all(32 < ord(c) < 127 for c in value),
            "exact_production_database_url_required")
        url = urlsplit(value)
        require(url.scheme in {"postgresql", "postgresql+psycopg"} and url.hostname == "postgres"
            and url.port in (None, 5432) and unquote(url.username or "") == "modish_user"
            and bool(url.password) and url.path == "/modish" and not url.query and not url.fragment
            and "\\" not in value, "exact_production_database_url_required")
    except (TypeError, ValueError):
        raise RuntimeError("exact_production_database_url_required") from None


def validate_environment():
    require(sys.platform == "linux" and os.geteuid() == 0, "linux_root_worker_required")
    require(os.getenv("REFRESH_RUN_ID") == RUN_ID and os.getenv("AUDIT_ARCHIVE_SHA256") == ARCHIVE
        and os.getenv("AUDIT_IMAGE_ID") == IMAGE and os.getenv("REFRESH_MANIFEST_SHA256") == MANIFEST,
        "exact_reviewed_artifact_environment_required")
    require(os.getenv("APP_ENV") == "production" and os.getenv("JWT_SECRET") == WORKER_JWT
        and os.getenv("PYTHON_DOTENV_DISABLED") == "1", "production_worker_environment_required")
    require(all(os.getenv(key) == "false" for key in DISABLED_FLAGS), "worker_integrations_must_be_disabled")
    require(not any(key.startswith("PG") for key in os.environ)
        and not os.getenv("AUDIT_FIXED_NOW_UTC")
        and all(not os.getenv(key) for key in PRIVATE_ENV)
        and not any(value for key, value in os.environ.items() if key.endswith("_API_KEY")),
        "no_worker_credentials_or_connection_overrides_required")
    # python-dotenv 1.0.1 ignores PYTHON_DOTENV_DISABLED: physical absence matters.
    require(not Path("/app/.env").exists() and not Path("/app/.env").is_symlink(),
        "image_dotenv_file_must_be_absent")
    validate_database_url(os.environ.get("DATABASE_URL"))


def require_private_directory(path):
    require(path.is_dir() and not path.is_symlink(), "private_outcome_directory_required")
    info = path.stat()
    require(info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o700, "private_outcome_mode_required")


def durable_json(path, value):
    raw = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
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


def start_ledger(plan):
    require_private_directory(OUTCOME_ROOT)
    require(not any(OUTCOME_ROOT.iterdir()), "existing_import_attempt_requires_review")
    durable_json(OUTCOME_ROOT / "started.json", {"operation": "build15_production_four_source_import",
        "run_id": RUN_ID, "plan_id": plan["plan_id"], "plan_sha256": os.environ["PLAN_SHA256"],
        "snapshot_manifest_sha256": MANIFEST, "started_at": datetime.now(timezone.utc).isoformat()})
    STATE["ledger_started"] = True


def verified_snapshots():
    require(FEED_ROOT.is_dir() and not FEED_ROOT.is_symlink(), "feed_directory_required")
    manifest_bytes = regular_bytes(FEED_ROOT / "download-manifest.json", 16384)
    require(hashlib.sha256(manifest_bytes).hexdigest() == MANIFEST, "pinned_download_manifest_required")
    manifest = json.loads(manifest_bytes)
    require(manifest.get("result") == "download_complete" and manifest.get("scope") ==
        "four_fixed_admitad_https_exports_no_database", "complete_download_manifest_required")
    require(manifest.get("observation_clock_basis") == "real_download_request_start_utc"
        and manifest.get("run_id") == RUN_ID, "matching_actual_observation_required")
    entries = manifest.get("sources", [])
    require(len(entries) == 4 and {r["code"] for r in entries} == set(FEEDS), "exact_source_set_required")
    now, verified = datetime.now(timezone.utc), {}
    for entry in entries:
        code = entry["code"]
        require(entry["feed_id"] == FEEDS[code] and entry["filename"] == code + ".csv", "fixed_snapshot_identity")
        path = FEED_ROOT / entry["filename"]
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
            keys = [value.strip().lower().replace(" ", "_") for value in header]
            require({"id", "name", "available", "price", "param"} <= set(keys)
                and bool({"currency", "currencyid"} & set(keys)) and len(set(keys)) == len(header), "expected_csv_header_required")
            for row in reader:
                if not row or not any(row):
                    continue
                require(len(row) == len(header), "malformed_csv_row")
                rows += 1
                require(rows <= MAX_ROWS, "snapshot_row_bound")
                values = dict(zip(keys, row))
                identity = next((values[key].strip() for key in IDENTITY_KEYS if values.get(key, "").strip()), "")
                if identity and len(identity) <= 128:
                    row_hash = hashlib.sha256(json.dumps(row, ensure_ascii=False).encode()).hexdigest()
                    if identity in identified:
                        duplicate_rows += 1
                        require(identified[identity] == row_hash, "conflicting_duplicate_identity_requires_review")
                    else:
                        identified[identity] = row_hash
        require(rows == REHEARSED_ROWS[code] and len(identified) == rows and duplicate_rows == 0,
            "exact_rehearsed_source_counts_required")
        require(rows >= PREVIOUS_ROWS[code] // 2, "unexpected_source_row_loss_requires_review")
        verified[code] = {**entry, "rows": rows, "distinct_identified_ids": len(identified),
            "observed": observed, "path": path}
    return manifest, verified


def identity_snapshot(db):
    from sqlalchemy import select
    from app.models import Product
    with db.execute(select(Product.id, Product.source, Product.source_id, Product.external_id)
            .where(Product.source.in_(tuple(FEEDS))).execution_options(yield_per=500)) as rows:
        return {row.id: (row.source, row.source_id, row.external_id) for row in rows}


def outside_state(db):
    """Hash every unaffected row, including non-target sync runs and user data.

    Only counts/hashes leave SQL; raw catalog/user values never enter the report.
    Ordering row hashes also handles tables without a single-column primary key.
    """
    from sqlalchemy import text
    targets = "('baon','shoppinglive','serginnetti','postmeridiem')"
    source_ids = "(SELECT id FROM public.product_sources WHERE code IN " + targets + ")"
    predicates = {"products": "source IS NULL OR source NOT IN " + targets,
        "catalog_records": "source_id IS NULL OR source_id NOT IN " + source_ids,
        "product_sources": "code IS NULL OR code NOT IN " + targets,
        "catalog_sync_runs": "source_id IS NULL OR source_id NOT IN " + source_ids}
    tables = list(db.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")))
    result = {}
    for table in tables:
        require(re.fullmatch(r"[a-z][a-z0-9_]*", table) is not None, "simple_public_table_names_required")
        query = ("SELECT count(*), md5(coalesce(string_agg(md5(row_to_json(r)::text), "
            "'' ORDER BY md5(row_to_json(r)::text)),'')) FROM public.\"" + table + '\" r WHERE ' + predicates.get(table, "TRUE"))
        result[table] = tuple(db.execute(text(query)).one())
    return result


def verify_physical_schema(db):
    from sqlalchemy import text
    require(db.scalar(text("SELECT version_num FROM alembic_version")) == TARGET_REVISION, "physical_schema_0028_required")
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


def commit_once(db, report):
    """Commit outcome remains uncertain on a transport/disk failure, never retried."""
    durable_json(OUTCOME_ROOT / "commit-intent.json", {"run_id": RUN_ID, "plan_id": report["plan_id"],
        "plan_sha256": report["plan_sha256"], "state": "commit_outcome_requires_review_until_confirmed"})
    STATE["transaction"] = "commit_attempted"
    db.commit()
    # Set this BEFORE any further filesystem/report operation can fail.
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


def main():
    plan = approved_plan()
    validate_environment()
    manifest, snapshots = verified_snapshots()  # ALL four before DB connection/write.
    start_ledger(plan)  # Never reuse even a failed/uncertain attempt directory.
    sys.path.insert(0, "/app")
    from sqlalchemy import func, select, text, update
    from app.db import SessionLocal, engine
    validate_database_url(engine.url.render_as_string(hide_password=False))
    from app.models import CatalogRecord, CatalogSyncRun, Product, ProductSource
    from app.integrations.admitad.feed_parser import parse_admitad_csv
    from app.services.catalog.catalog_records import store_record
    from app.services.catalog.feed_import_service import IngestResult, _finalize_run
    from app.services.catalog.versions import NORMALIZATION_VERSION
    require(NORMALIZATION_VERSION == "catalog_rules_v7", "reviewed_build15_pipeline_required")
    report = {"scope": "build15_approved_four_source_production_import", "run_id": RUN_ID,
        "plan_id": plan["plan_id"], "plan_sha256": os.environ["PLAN_SHA256"],
        "image_id": IMAGE, "archive_sha256": ARCHIVE, "snapshot_manifest_sha256": MANIFEST,
        "started_at": datetime.now(timezone.utc).isoformat(), "sources": [], "result": "running",
        "stock_observation_basis": "actual_download_request_start_utc", "single_transaction": True}
    with SessionLocal() as db:
        try:
            db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
            STATE["transaction"] = "open"
            db.execute(text("SET LOCAL statement_timeout = '120s'"))
            db.execute(text("SET LOCAL lock_timeout = '5s'"))
            identity = tuple(db.execute(text("SELECT current_database(), current_user, inet_server_port()")).one())
            require(identity == ("modish", "modish_user", 5432), "actual_production_database_identity_mismatch")
            verify_physical_schema(db)
            db.execute(text("SELECT pg_advisory_xact_lock(20261009, 20)"))
            sources = {s.code: s for s in db.scalars(select(ProductSource).where(
                ProductSource.code.in_(tuple(FEEDS))).with_for_update())}
            require(set(sources) == set(FEEDS), "exact_existing_sources_required")
            for code, source in sources.items():
                require(source.last_sync_at is None or source.last_sync_at < snapshots[code]["observed"],
                    "already_imported_or_newer_source_observation_requires_review")
                previous = int(db.scalar(select(func.count(func.distinct(CatalogRecord.external_id))).where(
                    CatalogRecord.source_id == source.id, CatalogRecord.external_id.is_not(None), CatalogRecord.status != "missing")) or 0)
                require(snapshots[code]["distinct_identified_ids"] >= max(PREVIOUS_ROWS[code] // 2, previous // 2),
                    "unexpected_current_source_identity_loss_requires_review")
                snapshots[code]["previous_distinct_identified_ids"] = previous
            existing_ids, other_rows_before = identity_snapshot(db), outside_state(db)
            for code in FEEDS:
                source, entry = sources[code], snapshots[code]
                # Hash again before parsing: /feeds must also be mounted read-only by host.
                raw = regular_bytes(entry["path"], MAX_BYTES)
                require(hashlib.sha256(raw).hexdigest() == entry["sha256"], "snapshot_changed_before_parse")
                rows = parse_admitad_csv(raw.decode("utf-8-sig"))
                del raw
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
                missing = db.execute(update(Product).where(Product.source_id == source.id, Product.source == code,
                    unseen_ids_predicate(Product.external_id, seen)).values(
                        is_deleted_from_feed=1, is_available=0, is_active=0, updated_at=processed))
                ingest.deactivated = missing.rowcount or 0
                db.execute(update(CatalogRecord).where(CatalogRecord.source_id == source.id,
                    CatalogRecord.external_id.is_not(None), unseen_ids_predicate(CatalogRecord.external_id, seen))
                    .values(status="missing", reasons=["missing_from_feed"]))
                ingest.ingested = ingest.created + ingest.updated
                _finalize_run(db, run=run, source=source, ingest=ingest, err=None, ok=True)
                item = {"code": code, "rows": ingest.total_received, "created": ingest.created,
                    "updated": ingest.updated, "quarantined": ingest.skipped, "missing": ingest.deactivated,
                    "reasons": bounded_reasons(ingest.skip_breakdown), "observed_at": entry["observed"].isoformat(),
                    "distinct_identified_ids": len(seen), "previous_distinct_identified_ids": entry["previous_distinct_identified_ids"],
                    "snapshot_sha256": entry["sha256"], "ordered_row_content_sha256": content_hash.hexdigest()}
                report["sources"].append(item)
                print(json.dumps({"progress": "source_prepared_not_committed", **item}), file=sys.stderr, flush=True)
                del rows
            db.flush()  # Include final source/run metadata in preservation comparisons.
            current_ids = identity_snapshot(db)
            require(all(current_ids.get(pid) == value for pid, value in existing_ids.items()), "existing_product_ids_changed")
            require(outside_state(db) == other_rows_before, "unaffected_rows_changed")
            require(all(datetime.now(timezone.utc) - timedelta(hours=72) <= entry["observed"]
                for entry in snapshots.values()), "observation_expired_before_commit")
            commit_once(db, report)
        except Exception:
            rollback_failure(db)
            raise
    report.update(result="production_import_committed", committed_sources=list(FEEDS),
        existing_product_ids_preserved=True, unaffected_rows_preserved=True, transaction_state="committed",
        needs_review=False, finished_at=datetime.now(timezone.utc).isoformat())
    durable_json(OUTCOME_ROOT / "committed.json", report)
    print(json.dumps(report), flush=True)


def failure_report(error):
    if STATE["transaction"] == "committed":
        STATE["needs_review"] = True
    return {"result": "failed", "error_type": type(error).__name__, "raw_error_omitted": True,
        "run_id": RUN_ID, "committed_sources": STATE["committed_sources"],
        "transaction_state": STATE["transaction"], "rollback_completed": STATE["rollback_completed"],
        "needs_review": STATE["needs_review"], "automatic_retry_allowed": False,
        "check": str(error) if isinstance(error, RuntimeError) and re.fullmatch(r"[a-z][a-z0-9_]{0,100}", str(error)) else None}


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        failure = failure_report(error)
        if STATE["ledger_started"]:
            try:
                durable_json(OUTCOME_ROOT / "failed.json", failure)
            except Exception:
                pass  # Existing started/commit-intent markers still forbid a retry.
        print(json.dumps(failure), flush=True)
        raise SystemExit(1) from None
