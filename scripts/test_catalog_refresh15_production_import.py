"""Offline approval, identity, freshness and commit-state guards; no DB/sockets."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent
SYNTHETIC_PASSWORD = "SYNTHETIC_PASSWORD_VALUE"
EXAMPLE_WRONG_TOKEN = "EXAMPLE_WRONG_TOKEN"


def load():
    spec = importlib.util.spec_from_file_location("production_import_guards", ROOT / "catalog_refresh15_production_import.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProductionImportGuards(unittest.TestCase):
    def setUp(self):
        self.m = load()

    def plan(self):
        m = self.m
        return {"operation": "build15_production_activation_plan", "plan_id": "3c905d3909274276a9b9ca8b9cb8dfe2",
            "run_id": m.RUN_ID, "archive_sha256": m.ARCHIVE, "image_id": m.IMAGE,
            "snapshot_manifest_sha256": m.MANIFEST, "source_revision": m.SOURCE_REVISION,
            "target_revision": m.TARGET_REVISION, "production_database": deepcopy(m.PRODUCTION_DATABASE),
            "feeds": deepcopy(m.FEEDS), "production_containers": deepcopy(m.PRODUCTION_CONTAINERS),
            "approval_required": True, "approval_token": m.APPROVAL}

    def environment(self):
        m = self.m
        return {"REFRESH_RUN_ID": m.RUN_ID, "AUDIT_ARCHIVE_SHA256": m.ARCHIVE, "AUDIT_IMAGE_ID": m.IMAGE,
            "REFRESH_MANIFEST_SHA256": m.MANIFEST, "APP_ENV": "production", "JWT_SECRET": m.WORKER_JWT,
            "PYTHON_DOTENV_DISABLED": "1", "DATABASE_URL": "postgresql+psycopg://modish_user:unused@postgres:5432/modish",
            **dict.fromkeys(m.DISABLED_FLAGS, "false")}

    def validate_env(self, env):
        with patch.dict(os.environ, env, clear=True), patch.object(self.m.sys, "platform", "linux"), \
                patch.object(self.m.os, "geteuid", lambda: 0, create=True):
            self.m.validate_environment()

    def test_missing_approval_blocks_before_plan_app_or_database(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(self.m, "regular_bytes") as read:
            with self.assertRaisesRegex(RuntimeError, "explicit_production_approval_required"):
                self.m.main()
            read.assert_not_called()

    def test_wrong_approval_is_not_authorization(self):
        with patch.dict(os.environ, {"PRODUCTION_APPROVAL_TOKEN": EXAMPLE_WRONG_TOKEN}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "explicit_production_approval_required"):
                self.m.approved_plan()

    def test_unpinned_plan_rejected(self):
        with patch.dict(os.environ, {"PRODUCTION_APPROVAL_TOKEN": self.m.APPROVAL}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "pinned_activation_plan_required"):
                self.m.approved_plan()

    def test_changed_plan_bytes_rejected(self):
        env = {"PRODUCTION_APPROVAL_TOKEN": self.m.APPROVAL, "PLAN_SHA256": "0" * 64}
        with patch.dict(os.environ, env, clear=True), patch.object(self.m, "regular_bytes", return_value=b"{}"):
            with self.assertRaisesRegex(RuntimeError, "activation_plan_hash_mismatch"):
                self.m.approved_plan()

    def test_complete_pinned_plan_accepts_extra_proof_fields(self):
        plan = self.plan()
        plan["proof_sha256"] = {"qa-report.json": "f" * 64}
        plan["production_database"]["extra_proof"] = True
        plan["production_containers"]["backend"]["writer_fence"] = "separate_host_gate"
        raw = json.dumps(plan).encode()
        env = {"PRODUCTION_APPROVAL_TOKEN": self.m.APPROVAL, "PLAN_SHA256": hashlib.sha256(raw).hexdigest()}
        with patch.dict(os.environ, env, clear=True), patch.object(self.m, "regular_bytes", return_value=raw):
            self.assertEqual(self.m.approved_plan(), plan)

    def test_changed_production_plan_bindings_rejected(self):
        for key, replacement in (("run_id", "f" * 32), ("archive_sha256", "f" * 64),
                ("image_id", "sha256:" + "f" * 64), ("snapshot_manifest_sha256", "f" * 64),
                ("target_revision", "20261013_0025"), ("approval_required", 1)):
            with self.subTest(key=key):
                plan = self.plan()
                plan[key] = replacement
                with self.assertRaisesRegex(RuntimeError, "exact_reviewed_activation_plan_required"):
                    self.m.validate_plan_value(plan)

    def test_no_old_or_non_uuid4_plan_id(self):
        for value in (self.m.RUN_ID, "a" * 32, "", "not-an-id"):
            with self.subTest(value=value), self.assertRaisesRegex(RuntimeError, "new_uuid4_plan_id_required"):
                self.m.validate_plan_value({**self.plan(), "plan_id": value})

    def test_wrong_container_or_database_binding_rejected(self):
        plan = self.plan()
        plan["production_containers"]["postgres"]["id"] = "0" * 64
        with self.assertRaisesRegex(RuntimeError, "exact_production_container_plan_required"):
            self.m.validate_plan_value(plan)
        plan = self.plan()
        plan["production_database"]["host"] = "localhost"
        with self.assertRaisesRegex(RuntimeError, "exact_production_database_plan_required"):
            self.m.validate_plan_value(plan)

    def test_exact_production_url_accepts_default_or_explicit_port(self):
        encoded = quote(SYNTHETIC_PASSWORD + "@/", safe="")
        for url in ("postgresql://modish_user:unused@postgres/modish",
                f"postgresql+psycopg://modish_user:{encoded}@postgres:5432/modish"):
            self.m.validate_database_url(url)

    def test_dangerous_database_urls_rejected_without_sqlalchemy(self):
        values = (None, "", "sqlite:///modish", "postgresql://modish_user:pw@localhost/modish",
            "postgresql://modish_user:pw@modish_postgres/modish", "postgresql://modish_user:pw@postgres:5433/modish",
            "postgresql://refresh15_uuid:pw@postgres/modish", "postgresql://modish_user:pw@postgres/other",
            "postgresql://modish_user:@postgres/modish", "postgresql://modish_user:pw@postgres/modish?host=evil",
            "postgresql://modish_user:pw@postgres/modish#fragment", "postgresql://modish_user:pw@postgres/modish?options=-crole=superuser",
            "postgresql://modish_user:pw@postgres:bad/modish", "\npostgresql://modish_user:pw@postgres/modish",
            "postgresql://modish_user:pw@postgres\\evil/modish", "postgresql://modish_user:pw@postgres/modish\n",
            "postgresql+psycopg2://modish_user:pw@postgres/modish")
        for value in values:
            with self.subTest(value=value), self.assertRaisesRegex(RuntimeError, "exact_production_database_url_required"):
                self.m.validate_database_url(value)

    def test_production_worker_environment_accepts_unused_jwt(self):
        self.validate_env(self.environment())

    def test_app_jwt_wrong_environment_and_active_integrations_rejected(self):
        for key, value, message in (("JWT_SECRET", "the-real-api-jwt-does-not-belong-here", "production_worker_environment_required"),
                ("APP_ENV", "test", "production_worker_environment_required"),
                ("AUDIT_IMAGE_ID", "sha256:" + "f" * 64, "exact_reviewed_artifact_environment_required"),
                ("ADMITAD_ENABLED", "true", "worker_integrations_must_be_disabled"),
                ("PYTHON_DOTENV_DISABLED", "", "production_worker_environment_required")):
            with self.subTest(key=key), self.assertRaisesRegex(RuntimeError, message):
                self.validate_env({**self.environment(), key: value})

    def test_api_credentials_pg_overrides_and_fixed_clock_rejected(self):
        for key in ("PGOPTIONS", "PGHOST", "PGPASSFILE", "PGDATABASE", "OPENAI_API_KEY", "AI_API_KEY",
                "ADMITAD_EXPORT_CODE", "OTHER_API_KEY", "AUDIT_FIXED_NOW_UTC"):
            with self.subTest(key=key), self.assertRaisesRegex(RuntimeError, "no_worker_credentials_or_connection_overrides_required"):
                self.validate_env({**self.environment(), key: "not-used"})

    def test_empty_pg_override_still_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "no_worker_credentials_or_connection_overrides_required"):
            self.validate_env({**self.environment(), "PGHOST": ""})

    def test_dotenv_file_rejected_even_with_disabled_flag(self):
        with patch.object(self.m.Path, "exists", return_value=True):
            with self.assertRaisesRegex(RuntimeError, "image_dotenv_file_must_be_absent"):
                self.validate_env(self.environment())

    def synthetic_snapshots(self, root, age=timedelta()):
        now, entries = datetime.now(timezone.utc), []
        for code, feed_id in self.m.FEEDS.items():
            raw = b"id;name;available;price;param;currency\n1;Example;true;10.25;;RUB\n"
            (root / (code + ".csv")).write_bytes(raw)
            entries.append({"code": code, "feed_id": feed_id, "filename": code + ".csv", "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(), "observed_at": (now - age).isoformat(),
                "download_finished_at": now.isoformat()})
        manifest = {"result": "download_complete", "scope": "four_fixed_admitad_https_exports_no_database",
            "observation_clock_basis": "real_download_request_start_utc", "run_id": self.m.RUN_ID, "sources": entries}
        raw = json.dumps(manifest).encode()
        (root / "download-manifest.json").write_bytes(raw)
        return hashlib.sha256(raw).hexdigest()

    def test_four_complete_fresh_hash_pinned_snapshots(self):
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            digest = self.synthetic_snapshots(root)
            with patch.object(self.m, "FEED_ROOT", root), patch.object(self.m, "MANIFEST", digest), \
                    patch.object(self.m, "REHEARSED_ROWS", dict.fromkeys(self.m.FEEDS, 1)), \
                    patch.object(self.m, "PREVIOUS_ROWS", dict.fromkeys(self.m.FEEDS, 2)):
                _, snapshots = self.m.verified_snapshots()
            self.assertEqual(set(snapshots), set(self.m.FEEDS))
            self.assertEqual(sum(entry["rows"] for entry in snapshots.values()), 4)

    def test_expired_actual_observation_cannot_be_renewed(self):
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            digest = self.synthetic_snapshots(root, age=timedelta(hours=73))
            with patch.object(self.m, "FEED_ROOT", root), patch.object(self.m, "MANIFEST", digest):
                with self.assertRaisesRegex(RuntimeError, "current_observation_bound"):
                    self.m.verified_snapshots()

    def test_exact_rehearsed_counts_required_even_after_hash_validation(self):
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            digest = self.synthetic_snapshots(root)
            with patch.object(self.m, "FEED_ROOT", root), patch.object(self.m, "MANIFEST", digest):
                with self.assertRaisesRegex(RuntimeError, "exact_rehearsed_source_counts_required"):
                    self.m.verified_snapshots()

    def test_last_source_corrupt_blocks_before_ledger_or_app_import(self):
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            digest = self.synthetic_snapshots(root)
            (root / "postmeridiem.csv").write_bytes(b"corrupt")
            with patch.object(self.m, "FEED_ROOT", root), patch.object(self.m, "MANIFEST", digest), \
                    patch.object(self.m, "REHEARSED_ROWS", dict.fromkeys(self.m.FEEDS, 1)), \
                    patch.object(self.m, "PREVIOUS_ROWS", dict.fromkeys(self.m.FEEDS, 2)), \
                    patch.object(self.m, "approved_plan", return_value=self.plan()), \
                    patch.object(self.m, "validate_environment"), patch.object(self.m, "start_ledger") as ledger:
                with self.assertRaisesRegex(RuntimeError, "snapshot_hash_mismatch"):
                    self.m.main()
                ledger.assert_not_called()

    def test_existing_ledger_forbids_attempt_reuse(self):
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            (root / "commit-intent.json").write_text("{}")
            with patch.object(self.m, "OUTCOME_ROOT", root), patch.object(self.m, "require_private_directory"), \
                    patch.object(self.m, "durable_json") as writer:
                with self.assertRaisesRegex(RuntimeError, "existing_import_attempt_requires_review"):
                    self.m.start_ledger(self.plan())
                writer.assert_not_called()

    def test_commit_transport_failure_remains_uncertain_after_rollback(self):
        db = Mock()
        db.commit.side_effect = ConnectionError("private connection details")
        with patch.object(self.m, "durable_json"):
            with self.assertRaises(ConnectionError):
                self.m.commit_once(db, {"plan_id": self.plan()["plan_id"], "plan_sha256": "f" * 64})
        self.m.rollback_failure(db)
        self.assertEqual(self.m.STATE["transaction"], "commit_outcome_uncertain")
        self.assertTrue(self.m.STATE["needs_review"])
        self.assertEqual(self.m.STATE["committed_sources"], [])
        self.assertNotIn("private connection details", json.dumps(self.m.failure_report(db.commit.side_effect)))

    def test_successful_commit_never_claims_rollback_on_disk_failure(self):
        db = Mock()
        with patch.object(self.m, "durable_json"):
            self.m.commit_once(db, {"plan_id": self.plan()["plan_id"], "plan_sha256": "f" * 64})
        self.m.rollback_failure(db)
        result = self.m.failure_report(OSError("disk full"))
        self.assertEqual(result["transaction_state"], "committed")
        self.assertEqual(result["committed_sources"], list(self.m.FEEDS))
        self.assertTrue(result["needs_review"])
        db.rollback.assert_not_called()

    def test_no_commit_if_durable_intent_write_fails(self):
        db = Mock()
        with patch.object(self.m, "durable_json", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.m.commit_once(db, {"plan_id": self.plan()["plan_id"], "plan_sha256": "f" * 64})
        db.commit.assert_not_called()

    @unittest.skipUnless(os.name == "posix", "directory fsync and root modes are Linux worker guards")
    def test_durable_json_exclusive_private_and_replay_rejected(self):
        with tempfile.TemporaryDirectory() as path:
            target = Path(path) / "started.json"
            self.m.durable_json(target, {"result": "private aggregate only"})
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                self.m.durable_json(target, {})

    def test_unrecognized_reason_and_raw_error_never_exported(self):
        self.assertEqual(self.m.bounded_reasons({"missing_size": 2, "private URL": 3}),
            {"missing_size": 2, "other_reason": 3})
        self.assertIsNone(self.m.failure_report(RuntimeError(f"postgresql://private:{SYNTHETIC_PASSWORD}@host/db"))["check"])


if __name__ == "__main__":
    unittest.main()
