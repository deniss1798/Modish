"""Prepare/run a fresh four-source experiment after immutable v20 validation.

This host-side runner contains no SSH. Main must review and upload the helpers
under new filenames before invoking it. Existing release outputs are immutable.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

R = Path("/opt/modish-releases/20261009-intent-v20")
FRESH = R / "refresh-20261013"
TEST_ENV = Path("/opt/modish-releases/20260922-foundation/test-api.env")
PG, NETWORK, OUTBOUND = "modish_foundation_test_pg", "modish-foundation-test", "modish_default"
BASE = "sha256:4ae234fc8a1f8082bb368f4f617d187a6138bb5848c02af5e9a7a9f15a3d6d7e"
FEEDS = {"baon": "19982", "shoppinglive": "25461", "serginnetti": "26417", "postmeridiem": "25200"}
HELPERS = {"download": "refresh-20261013-download.py", "import": "refresh-20261013-import.py",
    "readiness": "refresh-20261013-readiness.py", "inventory": "size-raw-inventory.py"}


def require(condition, code):
    if not condition:
        raise RuntimeError(code)


def inspect(name):
    return json.loads(subprocess.check_output(["docker", "inspect", name], stderr=subprocess.DEVNULL))[0]


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def write_report(name, report):
    with (FRESH / name).open("x") as output:
        json.dump(report, output, indent=2)
        output.write("\n")


def db_query(sql):
    return subprocess.check_output(["docker", "exec", PG, "psql", "-X", "-qAt", "-U", "modish_test",
        "-d", "modish_test", "--set", "ON_ERROR_STOP=1", "-c", sql], text=True, stderr=subprocess.DEVNULL).strip()


def guard_scope(pg_id=None):
    require(R.resolve() == R and R.is_dir() and not R.is_symlink(), "fixed_release_directory_required")
    container = inspect(PG)
    require(container["State"]["Running"] and (pg_id is None or container["Id"] == pg_id), "same_running_test_pg_required")
    require(set(container["NetworkSettings"]["Networks"]) == {NETWORK}, "test_pg_network_scope")
    require(not container["HostConfig"].get("PortBindings"), "test_pg_must_not_publish_ports")
    network = json.loads(subprocess.check_output(["docker", "network", "inspect", NETWORK]))[0]
    require(network["Internal"], "internal_test_network_required")
    values = dict(line.split("=", 1) for line in TEST_ENV.read_text().splitlines() if "=" in line and not line.startswith("#"))
    url = urlsplit(values["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://"))
    require(url.scheme == "postgresql" and url.hostname == PG and url.path == "/modish_test" and
        url.username == "modish_test" and url.port in (None, 5432) and not url.query and not url.fragment, "exact_test_env_database_required")
    require(db_query("SELECT current_database() || ':' || current_user") == "modish_test:modish_test", "actual_test_database_required")
    require(db_query("SELECT version_num FROM alembic_version") == "20261008_0024", "existing_v20_schema_required")
    require(db_query("SELECT count(*) FROM shopping_intents") == "0", "clean_test_intents_required")
    return container["Id"]


def production_snapshot():
    result = {}
    for name in ("modish_backend", "modish_postgres", "modish_redis"):
        container = inspect(name)
        require(container["State"]["Running"], "production_must_remain_running")
        result[name] = {"id": container["Id"], "image": container["Image"],
            "started_at": container["State"]["StartedAt"], "restart_count": container["RestartCount"]}
    require(result["modish_backend"]["image"] == BASE, "production_baseline_image_required")
    return result


def catalogue_snapshot():
    # PostgreSQL hashes complete rows internally; no raw row is returned.
    products = db_query("SELECT md5(coalesce(string_agg(md5(row_to_json(p)::text), '' ORDER BY p.id), '')) FROM products p")
    records = db_query("SELECT md5(coalesce(string_agg(md5(row_to_json(r)::text), '' ORDER BY r.id), '')) FROM catalog_records r")
    outside = db_query("SELECT md5(coalesce(string_agg(md5(row_to_json(p)::text), '' ORDER BY p.id), '')) FROM products p WHERE p.source NOT IN ('baon','shoppinglive','serginnetti','postmeridiem')")
    outside_records = db_query("SELECT md5(coalesce(string_agg(md5(row_to_json(r)::text), '' ORDER BY r.id), '')) FROM catalog_records r JOIN product_sources s ON s.id=r.source_id WHERE s.code NOT IN ('baon','shoppinglive','serginnetti','postmeridiem')")
    outside_sources = db_query("SELECT md5(coalesce(string_agg(md5(row_to_json(s)::text), '' ORDER BY s.id), '')) FROM product_sources s WHERE s.code NOT IN ('baon','shoppinglive','serginnetti','postmeridiem')")
    return {"products": int(db_query("SELECT count(*) FROM products")), "products_state_digest": products,
        "catalog_records_state_digest": records, "outside_source_products_state_digest": outside,
        "outside_catalog_records_state_digest": outside_records, "outside_sources_state_digest": outside_sources,
        "users": int(db_query("SELECT count(*) FROM users")),
        "shopping_intents": int(db_query("SELECT count(*) FROM shopping_intents"))}


def saved_export_config(run_id):
    # Read only the already-authorized saved configurations from the clone.
    # Raw URLs exist only in this subprocess pipe and Python memory.
    raw = db_query("BEGIN TRANSACTION READ ONLY; SELECT json_agg(json_build_object('code',code,'url',feed_url)) FROM product_sources WHERE code IN ('baon','shoppinglive','serginnetti','postmeridiem'); ROLLBACK;")
    entries = json.loads(raw)
    require(len(entries) == 4 and {row["code"] for row in entries} == set(FEEDS), "four_saved_exports_required")
    configs = []
    for row in entries:
        parsed = urlsplit(row["url"] or "")
        require(parsed.hostname == "export.admitad.com" and not parsed.username and not parsed.password and
            parsed.scheme in {"http", "https"} and parsed.port in (None, 80 if parsed.scheme == "http" else 443) and
            not parsed.fragment, "saved_export_origin_required")
        path = re.fullmatch(r"/ru/webmaster/websites/([0-9]+)/products/export_adv_products/", parsed.path)
        require(path is not None, "fixed_export_path_required")
        query = parse_qs(parsed.query, strict_parsing=True, keep_blank_values=True, max_num_fields=8)
        require(set(query) == {"user", "code", "feed_id", "format"} and all(len(v) == 1 for v in query.values()), "fixed_export_query_required")
        require(query["feed_id"] == [FEEDS[row["code"]]] and query["format"] == ["csv"], "fixed_authorized_feed_id_required")
        configs.append((path.group(1), query["user"][0], query["code"][0]))
    require(len(set(configs)) == 1, "shared_saved_export_credentials_required")
    website, user, code = configs[0]
    entries.clear()
    configs.clear()
    raw = None
    return {"website_id": website, "user": user, "code": code, "run_id": run_id}


def cleanup_container(name, image, helper, network):
    found = subprocess.run(["docker", "inspect", name], capture_output=True, text=True)
    if found.returncode:
        return
    container = json.loads(found.stdout)[0]
    require(container["Name"] == "/" + name and container["Image"] == image and
        container["Config"]["Cmd"] == ["/run.py"] and
        set(container["NetworkSettings"]["Networks"]) == {network}, "owned_helper_container_required")
    require(any(m["Source"] == str(helper) and m["Destination"] == "/run.py" and not m["RW"]
        for m in container["Mounts"]), "owned_helper_mount_required")
    subprocess.run(["docker", "rm", "-f", name], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def run_helper(label, args, hashes, *, run_id, pg_id, config=None):
    guard_scope(pg_id)
    helper = R / HELPERS[label]
    require(digest(helper) == hashes[label], "helper_changed")
    network = OUTBOUND if label == "download" else NETWORK
    name = "modish_v20_refresh_20261013_" + label + "_" + uuid4().hex
    command = ["docker", "run", "--rm", "--pull=never", "--name", name,
        "--cpus=1", "--memory=2048m" if label == "import" else "--memory=768m",
        "--cap-drop=ALL", "--security-opt=no-new-privileges", "--read-only", "--network", network,
        "-e", "PYTHON_DOTENV_DISABLED=1", "-e", "PYTHONDONTWRITEBYTECODE=1", "-e", "PYTHONOPTIMIZE=0", "-e", "APP_ENV=test",
        "-e", "REFRESH_RUN_ID=" + run_id, "-e", "AUDIT_IMAGE_ID=" + args.image_id,
        "-e", "AUDIT_ARCHIVE_SHA256=" + args.archive_sha256]
    if label != "download":
        command.extend(["--env-file", str(TEST_ENV)])
    else:
        command.append("-i")
    if label in {"download", "import"}:
        command.extend(["-v", str(FRESH / "feeds") + ":/feeds" + (":ro" if label == "import" else "")])
    command.extend(["-v", str(helper) + ":/run.py:ro", "--entrypoint", "python", args.image_id, "/run.py"])
    payload = json.dumps(config).encode() if config is not None else None
    try:
        with (FRESH / (label + "-report.json")).open("x") as output, (FRESH / (label + "-errors.log")).open("x") as error:
            subprocess.run(command, input=payload, stdout=output, stderr=error, check=True,
                timeout=1200 if label == "download" else 7200 if label in {"import", "readiness"} else 240)
    finally:
        payload = None
        cleanup_container(name, args.image_id, helper, network)
    require(digest(helper) == hashes[label], "helper_changed_after_run")
    return json.loads((FRESH / (label + "-report.json")).read_text())


def safe_import_outcome(phase):
    path = FRESH / "import-report.json"
    unknown = {"transaction_state": "commit_outcome_unknown", "committed_sources": [],
        "confirmed_rollback": False, "source": "helper_outcome_unavailable"}
    if not path.exists():
        return {**unknown, "transaction_state": "not_started" if phase in {"download", "backup"} else "commit_outcome_unknown"}
    try:
        require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 1024**2, "safe_outcome_file_required")
        value = json.loads(path.read_text())
        states = {"not_started", "open", "committed", "rolled_back", "commit_attempted", "commit_outcome_uncertain"}
        state = "committed" if value.get("result") == "import_committed" else value.get("transaction_state")
        require(state in states, "known_outcome_required")
        committed = value.get("committed_sources", [])
        require(isinstance(committed, list) and set(committed) <= set(FEEDS), "safe_committed_source_set_required")
        return {"transaction_state": state, "committed_sources": committed,
            "confirmed_rollback": state == "rolled_back" and value.get("rollback_completed") is True,
            "source": "sanitized_helper_report"}
    except Exception:
        return unknown


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive_sha256")
    parser.add_argument("image_id")
    for label in HELPERS:
        parser.add_argument("--" + label + "-sha256", required=True)
    args = parser.parse_args()
    hashes = {label: getattr(args, label + "_sha256") for label in HELPERS}
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", args.image_id) and
        all(re.fullmatch(r"[0-9a-f]{64}", value) for value in [args.archive_sha256, *hashes.values()]), "immutable_inputs_required")
    pg_id = guard_scope()
    production = production_snapshot()
    stage = json.loads((R / "stage-report.json").read_text())
    require(stage["result"] == "validation_complete_catalogue_readiness_unproven" and
        stage["production_unchanged"] and stage["image_id"] == args.image_id and
        stage["archive_sha256"] == args.archive_sha256 and stage["test_pg_container_id"] == pg_id,
        "completed_matching_v20_stage_required")
    require(digest(R / "source.tar") == args.archive_sha256 and inspect(args.image_id)["Id"] == args.image_id, "same_v20_artifact_required")
    for label, name in HELPERS.items():
        helper = R / name
        require(helper.is_file() and not helper.is_symlink() and digest(helper) == hashes[label], "pinned_new_helper_required")
    require(shutil.disk_usage(R).free >= 6 * 1024**3, "backup_and_snapshot_disk_space_required")
    require(not FRESH.exists(), "refresh_directory_already_exists")
    FRESH.mkdir(mode=0o700)
    (FRESH / "feeds").mkdir(mode=0o700)
    run_id = uuid4().hex
    report = {"scope": "fresh_four_sources_isolated_v20_experiment", "run_id": run_id,
        "started_at": datetime.now(timezone.utc).isoformat(), "image_id": args.image_id,
        "archive_sha256": args.archive_sha256, "helper_sha256": hashes, "test_pg_container_id": pg_id,
        "production_unchanged": False, "production_database_writes": False, "result": "running",
        "production_guard_basis": "container_identity_running_state_unchanged_and_all_database_writes_target_exact_internal_test_pg",
        "configuration_source": "read_only_saved_four_fixed_exports_in_isolated_clone", "phase": "download"}
    try:
        before = catalogue_snapshot()
        config = saved_export_config(run_id)
        try:
            downloaded = run_helper("download", args, hashes, run_id=run_id, pg_id=pg_id, config=config)
        finally:
            config.clear()
        require(downloaded["result"] == "download_complete" and downloaded["run_id"] == run_id, "complete_download_required")
        require(catalogue_snapshot() == before, "downloader_changed_catalogue")
        print("fresh_four_snapshots_downloaded", flush=True)
        report["phase"] = "backup"
        guard_scope(pg_id)
        backup = FRESH / "test-before-refresh.dump"
        with backup.open("xb") as output, (FRESH / "backup-errors.log").open("x") as error:
            subprocess.run(["docker", "exec", PG, "pg_dump", "-U", "modish_test", "-d", "modish_test",
                "--format=custom", "--no-owner", "--no-acl"], stdout=output, stderr=error, check=True, timeout=600)
        require(backup.stat().st_size > 0, "nonempty_clone_backup_required")
        with backup.open("rb") as stream:
            subprocess.run(["docker", "exec", "-i", PG, "pg_restore", "--list"], stdin=stream,
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
        require(catalogue_snapshot() == before, "clone_changed_before_backup_completed")
        report["backup"] = {"filename": backup.name, "bytes": backup.stat().st_size, "sha256": digest(backup),
            "scope": "existing_isolated_modish_test_only", "toc_verified": True}
        write_report("before-import.json", {"catalogue": before, "backup": report["backup"], "run_id": run_id})
        print("isolated_clone_backup_verified", flush=True)
        report["phase"] = "import"
        imported = run_helper("import", args, hashes, run_id=run_id, pg_id=pg_id)
        require(imported["result"] == "import_committed" and imported["single_transaction"] and
            imported["existing_product_ids_preserved"] and set(imported["committed_sources"]) == set(FEEDS), "atomic_import_required")
        after_import = catalogue_snapshot()
        require(all(after_import[key] == before[key] for key in ("outside_source_products_state_digest",
            "outside_catalog_records_state_digest", "outside_sources_state_digest")), "outside_authorized_sources_changed")
        report["phase"] = "inventory"
        inventory = run_helper("inventory", args, hashes, run_id=run_id, pg_id=pg_id)
        require(inventory["read_only_transaction"] and inventory["rollback_completed"] and
            not inventory["stock_timestamps_modified"], "readonly_size_inventory_required")
        report["phase"] = "readiness"
        readiness = run_helper("readiness", args, hashes, run_id=run_id, pg_id=pg_id)
        require(readiness["checks_passed"] and readiness["synthetic_users_rolled_back"] and
            len(readiness["profiles"]) == 10 and not readiness["stock_timestamps_modified"], "ten_profile_readiness_audit_required")
        require(catalogue_snapshot() == after_import, "post_import_audit_changed_catalogue")
        guard_scope(pg_id)
        require(production_snapshot() == production, "production_containers_changed")
        report.update(result="fresh_import_and_audit_complete", phase="complete", production_unchanged=True,
            product_ready=readiness["product_ready"], release_gate=readiness["release_gate"],
            catalogue_after_import=after_import, finished_at=datetime.now(timezone.utc).isoformat())
        write_report("refresh-report.json", report)
        print("fresh_refresh_complete " + str(report["product_ready"]), flush=True)
    except Exception as error:
        report.update(result="failed", error_type=type(error).__name__,
            import_outcome=safe_import_outcome(report["phase"]))
        if isinstance(error, subprocess.CalledProcessError):
            report['helper_exit_code'] = error.returncode
        try:
            report["production_unchanged"] = production_snapshot() == production
        except Exception as guard_error:
            report["final_guard_error_type"] = type(guard_error).__name__
        write_report("refresh-failure.json", report)
        print("fresh_refresh_failed " + report["error_type"], flush=True)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
