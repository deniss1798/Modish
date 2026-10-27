"""Offline build16 import guards. No application, SQL, sockets, or SSH."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import builtins
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import quote
from uuid import UUID


SOURCE = Path(__file__).with_name("catalog_refresh16_import.py")
RELEASE = "3c905d3909274276a9b9ca8b9cb8dfe2"
DOWNLOAD = "1e70688cc3fc4f12b2e8efcf18291535"
PRIVATE_SENTINEL = "SYNTHETIC_PRIVATE_VALUE_DO_NOT_LOG"


def load():
    spec = importlib.util.spec_from_file_location("build16_import_offline_guards", SOURCE)
    module = importlib.util.module_from_spec(spec)
    original_import = builtins.__import__

    def offline_import(name, *args, **kwargs):
        if name == "sqlalchemy" or name.startswith("sqlalchemy.") or name == "app" or name.startswith("app."):
            raise AssertionError("application or database module imported before preflight")
        return original_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=offline_import):
        spec.loader.exec_module(module)
    return module


class Build16ImportGuards(unittest.TestCase):
    def setUp(self):
        self.m = load()

    def plan(self, mode="clone"):
        database = {"host": "modish_foundation_test_pg", "database": "modish_refresh16_" + RELEASE,
            "role": "refresh16_" + RELEASE, "port": 5432}
        if mode == "production":
            database = {"host": "modish_postgres", "database": "modish", "role": "modish_user", "port": 5432}
        return {"schema_version": 1, "release": "build16", "mode": mode, "release_run_id": RELEASE,
            "download_run_id": DOWNLOAD, "image_id": "sha256:" + "1" * 64,
            "archive_sha256": "2" * 64, "manifest_sha256": "3" * 64, "worker_sha256": "4" * 64,
            "db": database, "schema": "20261015_0028"}

    def url(self, plan):
        db = plan["db"]
        return "postgresql+psycopg://" + db["role"] + ":" + quote(PRIVATE_SENTINEL + "@/", safe="") \
            + "@" + db["host"] + ":5432/" + db["database"]

    def environment(self, plan):
        return {"APP_ENV": "test" if plan["mode"] == "clone" else "production",
            "PYTHON_DOTENV_DISABLED": "1", "DATABASE_URL": self.url(plan),
            "AUDIT_IMAGE_ID": plan["image_id"], "AUDIT_ARCHIVE_SHA256": plan["archive_sha256"],
            "JWT_SECRET": PRIVATE_SENTINEL, "OPENAI_API_KEY": PRIVATE_SENTINEL,
            "ADMITAD_EXPORT_CODE": PRIVATE_SENTINEL, "ADMITAD_ENABLED": "true"}

    def validate_env(self, env, plan):
        with patch.dict(os.environ, env, clear=True), patch.object(self.m.sys, "platform", "linux"), \
                patch.object(self.m.os, "geteuid", lambda: 0, create=True):
            self.m.validate_environment(plan)

    def test_import_requires_no_app_or_database_dependencies(self):
        self.assertTrue(callable(self.m.validate_plan_value))

    def test_both_exact_database_modes_are_accepted(self):
        for mode in ("clone", "production"):
            with self.subTest(mode=mode):
                plan = self.plan(mode)
                self.assertEqual(self.m.validate_plan_value(plan), plan)
                self.m.validate_database_url(self.url(plan), plan)

    def test_plan_rejects_ambiguous_or_unreviewed_contract(self):
        changes = (("schema_version", True), ("schema_version", 2), ("release", "build15"),
            ("mode", "test"), ("mode", []), ("mode", {}),
            ("schema", "20261013_0025"), ("release_run_id", "a" * 32),
            ("download_run_id", "a" * 32), ("release_run_id", RELEASE.upper()),
            ("image_id", "modish:latest"), ("archive_sha256", "f" * 63),
            ("manifest_sha256", "F" * 64), ("worker_sha256", ""))
        for key, value in changes:
            with self.subTest(key=key, value=value), self.assertRaises(RuntimeError):
                self.m.validate_plan_value({**self.plan(), key: value})
        plan = self.plan()
        plan["unreviewed_option"] = True
        with self.assertRaises(RuntimeError):
            self.m.validate_plan_value(plan)

    def test_clone_never_accepts_production_or_another_release_database(self):
        for field, value in (("host", "modish_postgres"), ("database", "modish"),
                ("role", "modish_user"), ("database", "modish_refresh16_" + DOWNLOAD),
                ("role", "refresh16_" + DOWNLOAD), ("port", "5432"), ("port", 5433)):
            plan = self.plan()
            plan["db"][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(RuntimeError):
                self.m.validate_plan_value(plan)
        plan = self.plan()
        plan["db"]["options"] = "-c role=superuser"
        with self.assertRaises(RuntimeError):
            self.m.validate_plan_value(plan)

    def test_production_database_binding_is_exact(self):
        for field, value in (("host", "postgres"), ("database", "postgres"),
                ("role", "postgres"), ("port", 5433)):
            plan = self.plan("production")
            plan["db"][field] = value
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                self.m.validate_plan_value(plan)

    def test_database_url_cannot_override_plan_target_or_connection_options(self):
        for mode in ("clone", "production"):
            plan = self.plan(mode)
            valid = self.url(plan)
            urls = (None, "", "sqlite:///modish", valid.replace("5432", "5433"),
                valid.replace(plan["db"]["host"], "localhost"), valid + "?host=other",
                valid + "?options=-crole=postgres", valid + "#fragment", "\n" + valid,
                valid + "\n", valid.replace("postgresql+psycopg", "postgresql+psycopg2"),
                valid.replace(quote(PRIVATE_SENTINEL + "@/", safe=""), ""))
            for value in urls:
                with self.subTest(mode=mode, value=value), self.assertRaises(RuntimeError):
                    self.m.validate_database_url(value, plan)

    def test_cross_mode_database_urls_are_rejected(self):
        with self.assertRaises(RuntimeError):
            self.m.validate_database_url(self.url(self.plan("production")), self.plan())
        with self.assertRaises(RuntimeError):
            self.m.validate_database_url(self.url(self.plan()), self.plan("production"))

    def test_production_application_environment_is_preserved(self):
        plan = self.plan("production")
        env = self.environment(plan)
        with patch.dict(os.environ, env, clear=True), patch.object(self.m.sys, "platform", "linux"), \
                patch.object(self.m.os, "geteuid", lambda: 0, create=True):
            before = dict(os.environ)
            self.m.validate_environment(plan)
            self.assertEqual(dict(os.environ), before)

    def test_runtime_mode_provenance_and_dotenv_flag_must_match(self):
        for mode in ("clone", "production"):
            plan = self.plan(mode)
            for key, value in (("APP_ENV", "production" if mode == "clone" else "test"),
                    ("PYTHON_DOTENV_DISABLED", "0"), ("AUDIT_IMAGE_ID", "sha256:" + "f" * 64),
                    ("AUDIT_ARCHIVE_SHA256", "f" * 64)):
                with self.subTest(mode=mode, key=key), self.assertRaises(RuntimeError):
                    self.validate_env({**self.environment(plan), key: value}, plan)

    def test_pg_overrides_and_fixed_clock_are_rejected_even_if_empty(self):
        plan = self.plan()
        for key in ("PGHOST", "PGHOSTADDR", "PGDATABASE", "PGOPTIONS", "PGSERVICEFILE",
                "PGPASSFILE", "PGUSER", "PGPORT", "AUDIT_FIXED_NOW_UTC"):
            for value in ("", "override"):
                with self.subTest(key=key, value=value), self.assertRaises(RuntimeError):
                    self.validate_env({**self.environment(plan), key: value}, plan)

    def test_deterministic_run_ids_are_bound_to_release_mode_and_source(self):
        clone, production = self.plan(), self.plan("production")
        clone_ids = {code: self.m.deterministic_run_id(clone, code) for code in self.m.FEEDS}
        production_ids = {code: self.m.deterministic_run_id(production, code) for code in self.m.FEEDS}
        self.assertEqual(len(set(clone_ids.values()) | set(production_ids.values())), 8)
        self.assertEqual(clone_ids, {code: self.m.deterministic_run_id(deepcopy(clone), code) for code in self.m.FEEDS})
        self.assertTrue(all(UUID(value).version == 5 for value in [*clone_ids.values(), *production_ids.values()]))
        other = self.plan()
        other["release_run_id"] = DOWNLOAD
        self.assertNotEqual(self.m.deterministic_run_id(other, "baon"), clone_ids["baon"])

    def test_unknown_source_cannot_gain_deterministic_run_identity(self):
        with self.assertRaises(RuntimeError):
            self.m.deterministic_run_id(self.plan(), "unreviewed_source")

    def test_plan_bytes_and_worker_bytes_must_both_match_pins(self):
        with tempfile.TemporaryDirectory() as path:
            target = Path(path) / "plan.json"
            plan = self.plan()
            plan["worker_sha256"] = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
            raw = json.dumps(plan).encode()
            target.write_bytes(raw)
            self.assertEqual(self.m.approved_plan(target, hashlib.sha256(raw).hexdigest()), plan)
            with self.assertRaises(RuntimeError):
                self.m.approved_plan(target, "0" * 64)
            plan["worker_sha256"] = "0" * 64
            raw = json.dumps(plan).encode()
            target.write_bytes(raw)
            with self.assertRaises(RuntimeError):
                self.m.approved_plan(target, hashlib.sha256(raw).hexdigest())

    def test_unpinned_plan_blocks_main_before_any_application_import(self):
        with tempfile.TemporaryDirectory() as path:
            target = Path(path) / "plan.json"
            target.write_text("{}", encoding="utf-8")
            original_import = builtins.__import__
            app_imports = []

            def guard(name, *args, **kwargs):
                if name == "app" or name.startswith("app.") or name == "sqlalchemy" or name.startswith("sqlalchemy."):
                    app_imports.append(name)
                    raise AssertionError("application import occurred before pinned input checks")
                return original_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=guard), self.assertRaises(RuntimeError):
                self.m.main(["--plan", str(target), "--plan-sha256", "0" * 64])
            self.assertEqual(app_imports, [])

    def test_both_rows_and_distinct_identity_loss_have_exact_half_boundary(self):
        for code, previous in self.m.PREVIOUS_ROWS.items():
            boundary = (previous + 1) // 2
            with self.subTest(code=code):
                self.m.validate_source_counts(code, rows=boundary, distinct=boundary)
                with self.assertRaises(RuntimeError):
                    self.m.validate_source_counts(code, rows=boundary - 1, distinct=boundary - 1)
                with self.assertRaises(RuntimeError):
                    self.m.validate_source_counts(code, rows=boundary, distinct=boundary - 1)

    def test_live_previous_counts_strengthen_row_loss_guard(self):
        code = "baon"
        previous = self.m.PREVIOUS_ROWS[code] * 2 + 1
        boundary = (previous + 1) // 2
        with self.assertRaises(RuntimeError):
            self.m.validate_source_counts(code, rows=boundary - 1, distinct=boundary - 1,
                previous_rows=previous)
        with self.assertRaises(RuntimeError):
            self.m.validate_source_counts(code, rows=boundary - 1, distinct=boundary - 1,
                previous_distinct=previous)
        self.m.validate_source_counts(code, rows=boundary, distinct=boundary,
            previous_rows=previous, previous_distinct=previous)

    def test_retained_historical_products_do_not_inflate_previous_feed_baseline(self):
        # Successful feed inputs were 9,318 / 378. Product history retains missing
        # offers too (19,246 / 752), so it is not the prior snapshot baseline.
        for code, prior_input, current_input, half_loss in (
                ("baon", 9318, 9620, 4658), ("postmeridiem", 378, 374, 188)):
            with self.subTest(code=code):
                self.m.validate_source_counts(code, rows=current_input, distinct=current_input,
                    previous_rows=prior_input, previous_distinct=prior_input)
                with self.assertRaises(RuntimeError):
                    self.m.validate_source_counts(code, rows=half_loss, distinct=half_loss,
                        previous_rows=prior_input, previous_distinct=prior_input)

    def test_available_and_quality_positive_guard_has_exact_eighty_percent_boundary(self):
        for code, baseline in self.m.BASELINE_POSITIVE.items():
            boundary = (baseline * 4 + 4) // 5
            rows = self.m.PREVIOUS_ROWS[code]
            with self.subTest(code=code):
                self.m.validate_source_counts(code, rows=rows, distinct=rows, available_quality=boundary)
                with self.assertRaises(RuntimeError):
                    self.m.validate_source_counts(code, rows=rows, distinct=rows, available_quality=boundary - 1)
                stronger = baseline * 2
                with self.assertRaises(RuntimeError):
                    self.m.validate_source_counts(code, rows=rows, distinct=rows,
                        available_quality=boundary, baseline_available_quality=stronger)

    def synthetic_snapshots(self, root, *, age=timedelta(), extra_row=False):
        now, entries = datetime.now(timezone.utc), []
        for code, feed_id in self.m.FEEDS.items():
            raw = b"id;name;available;price;param;currency\n1;Example;true;10.25;;RUB\n"
            if extra_row:
                raw += b"1;Changed duplicate;true;11.25;;RUB\n"
            (root / (code + ".csv")).write_bytes(raw)
            entries.append({"code": code, "feed_id": feed_id, "filename": code + ".csv", "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(), "observed_at": (now - age).isoformat(),
                "download_finished_at": now.isoformat()})
        manifest = {"result": "download_complete", "scope": "four_fixed_admitad_https_exports_no_database",
            "observation_clock_basis": "real_download_request_start_utc", "run_id": DOWNLOAD, "sources": entries}
        raw = json.dumps(manifest).encode()
        (root / "download-manifest.json").write_bytes(raw)
        return hashlib.sha256(raw).hexdigest()

    def test_all_four_pinned_current_snapshots_verify_without_database(self):
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            plan = {**self.plan(), "manifest_sha256": self.synthetic_snapshots(root)}
            with patch.object(self.m, "FEED_ROOT", root), \
                    patch.object(self.m, "PREVIOUS_ROWS", dict.fromkeys(self.m.FEEDS, 2)):
                _, snapshots = self.m.verified_snapshots(plan)
            self.assertEqual(set(snapshots), set(self.m.FEEDS))
            self.assertTrue(all(entry["rows"] == entry["distinct_identified_ids"] == 1 for entry in snapshots.values()))

    def test_expired_download_timestamp_cannot_be_renewed_by_processing(self):
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            plan = {**self.plan(), "manifest_sha256": self.synthetic_snapshots(root, age=timedelta(hours=73))}
            with patch.object(self.m, "FEED_ROOT", root), self.assertRaises(RuntimeError):
                self.m.verified_snapshots(plan)

    def test_manifest_is_bound_to_download_run_and_snapshot_bytes(self):
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            plan = {**self.plan(), "manifest_sha256": self.synthetic_snapshots(root)}
            plan["download_run_id"] = RELEASE
            with patch.object(self.m, "FEED_ROOT", root), self.assertRaises(RuntimeError):
                self.m.verified_snapshots(plan)
            plan["download_run_id"] = DOWNLOAD
            (root / "postmeridiem.csv").write_bytes(b"corrupted")
            with patch.object(self.m, "FEED_ROOT", root), \
                    patch.object(self.m, "PREVIOUS_ROWS", dict.fromkeys(self.m.FEEDS, 2)), self.assertRaises(RuntimeError):
                self.m.verified_snapshots(plan)

    def test_conflicting_duplicate_offer_is_rejected_before_any_database_import(self):
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            plan = {**self.plan(), "manifest_sha256": self.synthetic_snapshots(root, extra_row=True)}
            with patch.object(self.m, "FEED_ROOT", root), \
                    patch.object(self.m, "PREVIOUS_ROWS", dict.fromkeys(self.m.FEEDS, 2)), self.assertRaises(RuntimeError):
                self.m.verified_snapshots(plan)

    def report(self):
        plan = self.plan()
        return {"release_run_id": RELEASE, "mode": "clone", "plan_sha256": "f" * 64,
            "sync_run_ids": {code: self.m.deterministic_run_id(plan, code) for code in self.m.FEEDS}}

    def test_existing_attempt_or_uncertain_commit_ledger_cannot_be_reused(self):
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            (root / "commit-intent.json").write_text("{}", encoding="utf-8")
            with patch.object(self.m, "OUTCOME_ROOT", root), patch.object(self.m, "require_private_directory"), \
                    patch.object(self.m, "durable_json") as writer, self.assertRaises(RuntimeError):
                self.m.start_ledger(self.plan(), "f" * 64)
            writer.assert_not_called()

    def test_all_31_tables_and_historical_target_sync_runs_remain_protected(self):
        plan = self.plan()
        tables = ["catalog_sync_runs", "catalog_records", "products", "product_sources"] \
            + ["unaffected_" + str(index) for index in range(27)]
        db = Mock()
        db.scalars.return_value = tables
        db.execute.return_value = SimpleNamespace(one=lambda: (17, "a" * 32))
        fake_sqlalchemy = SimpleNamespace(text=lambda value: value)
        original_import = builtins.__import__

        def forbid_app(name, *args, **kwargs):
            if name == "app" or name.startswith("app."):
                raise AssertionError("preservation guard must not import application modules")
            return original_import(name, *args, **kwargs)

        with patch.dict(sys.modules, {"sqlalchemy": fake_sqlalchemy}), \
                patch.object(self.m, "CURRENT_PLAN", plan), patch("builtins.__import__", side_effect=forbid_app):
            result = self.m.outside_state(db)
            self.assertEqual(set(result), set(tables))
            self.assertEqual(len(db.execute.call_args_list), 31)
            queries = [call.args[0] for call in db.execute.call_args_list]
            sync_query = next(query for query in queries if 'FROM public."catalog_sync_runs"' in query)
            self.assertIn("md5(row_to_json(r)::text)", sync_query)
            predicate = sync_query.split(" WHERE ", 1)[1]
            expected_ids = [self.m.deterministic_run_id(plan, code) for code in self.m.FEEDS]
            self.assertEqual(predicate, "id NOT IN (" + ",".join("'" + value + "'" for value in expected_ids) + ")")
            self.assertNotIn("source_id", predicate)
            self.assertEqual(result["catalog_sync_runs"], (17, "a" * 32))
            for invalid_tables in (tables[:-1], tables + ["extra_table"], tables[:-1] + [tables[0]]):
                bad_db = Mock()
                bad_db.scalars.return_value = invalid_tables
                with self.subTest(table_count=len(invalid_tables)), self.assertRaises(RuntimeError):
                    self.m.outside_state(bad_db)
                bad_db.execute.assert_not_called()

    def test_no_database_commit_without_durable_intent(self):
        db = Mock()
        with patch.object(self.m, "durable_json", side_effect=OSError("disk full")), self.assertRaises(OSError):
            self.m.commit_once(db, self.report())
        db.commit.assert_not_called()

    def test_commit_transport_failure_stays_uncertain_after_rollback(self):
        db = Mock()
        db.commit.side_effect = ConnectionError(PRIVATE_SENTINEL)
        with patch.object(self.m, "durable_json"), self.assertRaises(ConnectionError):
            self.m.commit_once(db, self.report())
        self.m.rollback_failure(db)
        report = self.m.failure_report(db.commit.side_effect)
        self.assertEqual(report["transaction_state"], "commit_outcome_uncertain")
        self.assertTrue(report["needs_review"])
        self.assertFalse(report["automatic_retry_allowed"])
        self.assertNotIn(PRIVATE_SENTINEL, json.dumps(report))

    def test_successful_commit_cannot_be_relabelled_rolled_back_on_outcome_failure(self):
        db = Mock()
        with patch.object(self.m, "durable_json"):
            self.m.commit_once(db, self.report())
        self.m.rollback_failure(db)
        report = self.m.failure_report(OSError(PRIVATE_SENTINEL))
        self.assertEqual(report["transaction_state"], "committed")
        self.assertEqual(report["committed_sources"], list(self.m.FEEDS))
        self.assertTrue(report["needs_review"])
        self.assertNotIn(PRIVATE_SENTINEL, json.dumps(report))
        db.rollback.assert_not_called()

    @unittest.skipUnless(os.name == "posix", "root modes and directory fsync are Linux worker guards")
    def test_durable_outcome_is_private_exclusive_and_synced(self):
        with tempfile.TemporaryDirectory() as path:
            target = Path(path) / "started.json"
            with patch.object(self.m.os, "fsync", wraps=os.fsync) as sync:
                self.m.durable_json(target, {"status": "safe aggregates"})
            self.assertGreaterEqual(sync.call_count, 2)
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                self.m.durable_json(target, {})

    def test_raw_exception_and_unknown_reason_never_leak(self):
        report = self.m.failure_report(RuntimeError("postgresql://private:" + PRIVATE_SENTINEL + "@host/db"))
        self.assertNotIn(PRIVATE_SENTINEL, json.dumps(report))
        self.assertEqual(self.m.bounded_reasons({"missing_size": 2, PRIVATE_SENTINEL: 3}),
            {"missing_size": 2, "other_reason": 3})


if __name__ == "__main__":
    unittest.main()
