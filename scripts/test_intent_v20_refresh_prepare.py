"""Offline safety checks for new refresh helpers; no SSH, DB or real HTTP."""
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlencode, urlsplit

ROOT = Path(__file__).resolve().parent.parent


def load(name):
    path = ROOT / "scripts" / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DOWNLOAD = load("intent_v20_refresh_download")
IMPORT = load("intent_v20_refresh_import")
REMOTE = load("intent_v20_refresh_remote")
CSV = "id;name;available;price;param;currencyId\nsynthetic;Туфли;true;2000;Размер:38;RUB\n".encode()


class FakeResponse:
    def __init__(self, status=200, body=CSV):
        self.status_code, self.body = status, body
        self.headers = {"content-type": "text/csv"}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_bytes(self, **kwargs):
        yield self.body


class RefreshPrepareTests(unittest.TestCase):
    def setUp(self):
        base = (ROOT / ".tmp").resolve()
        self.assertTrue(base.is_relative_to(ROOT.resolve()))
        self.folder = tempfile.TemporaryDirectory(prefix="v20-refresh-offline-", dir=base)
        self.target = Path(self.folder.name).resolve()
        self.assertTrue(self.target.is_relative_to(base))
        self.addCleanup(self.folder.cleanup)

    def saved_urls(self):
        return [{"code": code, "url": "http://export.admitad.com/ru/webmaster/websites/123/products/export_adv_products/?" +
            urlencode({"user": "synthetic-user", "code": "synthetic-code", "feed_id": feed, "format": "csv"})}
            for code, feed in REMOTE.FEEDS.items()]

    def manifest(self):
        now = datetime.now(timezone.utc)
        entries = []
        for code, feed in IMPORT.FEEDS.items():
            (self.target / (code + ".csv")).write_bytes(CSV)
            entries.append({"code": code, "feed_id": feed, "filename": code + ".csv", "bytes": len(CSV),
                "sha256": hashlib.sha256(CSV).hexdigest(), "observed_at": now.isoformat(), "download_finished_at": now.isoformat()})
        value = {"result": "download_complete", "scope": "four_fixed_admitad_https_exports_no_database",
            "run_id": "1" * 32, "observation_clock_basis": "real_download_request_start_utc", "sources": entries}
        self.write_manifest(value)
        return value

    def write_manifest(self, value):
        (self.target / "download-manifest.json").write_text(json.dumps(value), encoding="utf-8")

    def verify(self):
        with (patch.object(IMPORT, "TARGET", self.target), patch.object(IMPORT, "PREVIOUS_ROWS", {code: 2 for code in IMPORT.FEEDS}),
                patch.dict(os.environ, {"REFRESH_RUN_ID": "1" * 32})):
            return IMPORT.verified_snapshots()

    def test_saved_configs_only_accept_fixed_four_authorized_exports(self):
        with patch.object(REMOTE, "db_query", return_value=json.dumps(self.saved_urls())):
            result = REMOTE.saved_export_config("1" * 32)
        self.assertEqual(set(result), {"website_id", "user", "code", "run_id"})
        self.assertEqual(result["website_id"], "123")

    def test_saved_url_origin_credentials_and_feed_id_guards(self):
        for mutation in (lambda s: s.replace("export.admitad.com", "evil.invalid"),
                lambda s: s.replace("http://", "http://synthetic:synthetic@"),
                lambda s: s + "&user=second", lambda s: s.replace("feed_id=19982", "feed_id=99999"),
                lambda s: s + "#fragment", lambda s: s.replace("export.admitad.com/", "export.admitad.com:8443/")):
            rows = self.saved_urls()
            rows[0]["url"] = mutation(rows[0]["url"])
            with patch.object(REMOTE, "db_query", return_value=json.dumps(rows)):
                with self.assertRaises(RuntimeError):
                    REMOTE.saved_export_config("1" * 32)

    def run_download(self, *, status=200, body=CSV, max_bytes=None):
        requested, options = [], []
        class Client:
            def __init__(self, **kwargs):
                options.append(kwargs)
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def stream(self, method, url):
                requested.append(url)
                return FakeResponse(status=status, body=body)
        config = {"website_id": "123", "user": "synthetic-user", "code": "synthetic-code", "run_id": "1" * 32}
        stdin = SimpleNamespace(buffer=io.BytesIO(json.dumps(config).encode()))
        output = io.StringIO()
        def exclusive_publish(source, destination):
            # Windows sandbox denies hard links; emulate the exclusive Linux
            # publication primitive while testing all network/data guards.
            with destination.open("xb") as target:
                target.write(source.read_bytes())
        with (patch.object(DOWNLOAD, "TARGET", self.target), patch.object(DOWNLOAD.httpx, "Client", Client),
                patch.object(DOWNLOAD.sys, "stdin", stdin), patch.dict(os.environ, {"AUDIT_FIXED_NOW_UTC": ""}),
                patch.object(DOWNLOAD, "MAX_BYTES", max_bytes or DOWNLOAD.MAX_BYTES),
                patch.object(DOWNLOAD.os, "link", side_effect=exclusive_publish),
                redirect_stdout(output), redirect_stderr(io.StringIO())):
            DOWNLOAD.main()
        return requested, options, output.getvalue()

    def test_download_reconstructs_https_and_never_persists_credentials(self):
        urls, options, output = self.run_download()
        self.assertEqual(len(urls), 4)
        self.assertTrue(all(urlsplit(url).scheme == "https" and urlsplit(url).hostname == "export.admitad.com" for url in urls))
        self.assertEqual(options[0]["trust_env"], False)
        self.assertEqual(options[0]["follow_redirects"], False)
        report = json.loads(output)
        self.assertEqual(report["result"], "download_complete")
        self.assertFalse(any(path.suffix == ".partial" for path in self.target.iterdir()))
        for text in [output, *(path.read_text(encoding="utf-8") for path in self.target.iterdir())]:
            self.assertNotIn("synthetic-code", text)
            self.assertNotIn("synthetic-user", text)
            self.assertNotIn("export.admitad.com/", text)

    def test_redirect_or_oversized_feed_never_gets_complete_manifest(self):
        for status, bound in ((302, None), (200, 8)):
            with self.assertRaises(RuntimeError):
                self.run_download(status=status, max_bytes=bound)
            self.assertFalse((self.target / "download-manifest.json").exists())

    def test_complete_manifest_is_verified_without_changing_observation(self):
        value = self.manifest()
        manifest, verified = self.verify()
        self.assertEqual(manifest, value)
        self.assertEqual(verified["baon"]["observed"].isoformat(), value["sources"][0]["observed_at"])
        self.assertEqual({v["rows"] for v in verified.values()}, {1})
        self.assertEqual({v["distinct_identified_ids"] for v in verified.values()}, {1})

    def test_duplicate_rows_cannot_bypass_distinct_identity_loss_guard(self):
        value = self.manifest()
        body = CSV + CSV.splitlines(keepends=True)[1] * 5
        (self.target / "shoppinglive.csv").write_bytes(body)
        entry = next(e for e in value["sources"] if e["code"] == "shoppinglive")
        entry.update(bytes=len(body), sha256=hashlib.sha256(body).hexdigest())
        self.write_manifest(value)
        with (patch.object(IMPORT, "TARGET", self.target),
                patch.object(IMPORT, "PREVIOUS_ROWS", {code: 6 if code == "shoppinglive" else 2 for code in IMPORT.FEEDS}),
                patch.dict(os.environ, {"REFRESH_RUN_ID": "1" * 32})):
            with self.assertRaises(RuntimeError): IMPORT.verified_snapshots()

    def test_conflicting_duplicate_id_is_rejected_before_db_writes(self):
        value = self.manifest()
        body = CSV + CSV.splitlines(keepends=True)[1].replace(b"2000", b"3000")
        (self.target / "baon.csv").write_bytes(body)
        value["sources"][0].update(bytes=len(body), sha256=hashlib.sha256(body).hexdigest())
        self.write_manifest(value)
        with self.assertRaises(RuntimeError): self.verify()

    def test_hash_mismatch_prevents_preflight_before_writes(self):
        self.manifest()
        (self.target / "baon.csv").write_bytes(CSV + b"truncated")
        with self.assertRaises(RuntimeError): self.verify()

    def test_old_or_future_observation_cannot_be_renewed_by_import(self):
        value = self.manifest()
        for timestamp in (datetime.now(timezone.utc) - timedelta(days=4), datetime.now(timezone.utc) + timedelta(days=1)):
            value["sources"][0]["observed_at"] = timestamp.isoformat()
            self.write_manifest(value)
            with self.assertRaises(RuntimeError): self.verify()

    def test_incomplete_source_set_or_path_override_is_rejected(self):
        value = self.manifest()
        value["sources"][0]["filename"] = "../baon.csv"
        self.write_manifest(value)
        with self.assertRaises(RuntimeError): self.verify()
        value["sources"] = value["sources"][:3]
        self.write_manifest(value)
        with self.assertRaises(RuntimeError): self.verify()

    def test_import_production_or_query_override_is_rejected_before_app_import(self):
        for value in ("postgresql://modish_test:synthetic@production.invalid/modish_test",
                "postgresql://modish_test:synthetic@modish_foundation_test_pg/modish_test?host=production.invalid"):
            with patch.dict(os.environ, {"DATABASE_URL": value}):
                with self.assertRaises(RuntimeError): IMPORT.main()

    def test_large_missing_id_set_uses_one_array_bind_without_db(self):
        from sqlalchemy import column, select
        from sqlalchemy.dialects.postgresql import dialect
        values = {str(index) for index in range(100000)}
        query = select(column("external_id")).where(IMPORT.unseen_ids_predicate(column("external_id"), values))
        compiled = query.compile(dialect=dialect())
        self.assertIn("ALL", str(compiled))
        self.assertEqual(set(compiled.params), {"seen_external_ids"})
        self.assertEqual(set(compiled.params["seen_external_ids"]), values)

    def test_failed_runner_keeps_commit_or_uncertain_outcome(self):
        path = self.target / "import-report.json"
        with patch.object(REMOTE, "FRESH", self.target):
            self.assertEqual(REMOTE.safe_import_outcome("download")["transaction_state"], "not_started")
            self.assertEqual(REMOTE.safe_import_outcome("import")["transaction_state"], "commit_outcome_unknown")
            for result, state in (("import_committed", "committed"), ("failed", "commit_outcome_uncertain"), ("failed", "rolled_back")):
                path.write_text(json.dumps({"result": result, "transaction_state": state, "committed_sources": list(REMOTE.FEEDS)
                    if state == "committed" else [], "rollback_completed": state == "rolled_back"}))
                outcome = REMOTE.safe_import_outcome("readiness")
                self.assertEqual(outcome["transaction_state"], state)
                self.assertEqual(outcome["confirmed_rollback"], state == "rolled_back")


if __name__ == "__main__":
    unittest.main()
