import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch


SCANNER_PATH = Path(__file__).resolve().parents[2] / "scripts" / "scan_secrets.py"
SPEC = importlib.util.spec_from_file_location("secret_scan", SCANNER_PATH)
scanner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scanner)


class SecretScanTests(unittest.TestCase):
    def test_comparison_to_a_variable_is_not_a_literal_assignment(self):
        for operator in ("==", "==="):
            statement = "require(approval_token " + operator + " APPROVAL, 'explicit_approval_required')"
            self.assertEqual(scanner.findings(statement), [])

    def test_literal_secret_in_comparison_is_still_detected(self):
        value = "opaque" + "42" * 12
        for operator in ("=", "==", "==="):
            statement = "JWT_SECRET " + operator + " " + repr(value)
            self.assertIn("credential_assignment", scanner.findings(statement))

    def test_provider_keys_are_rejected_even_with_placeholder_substrings(self):
        for prefix in ("sk-", "sk-proj-"):
            for marker in ("example", "synthetic", "redacted", "your_"):
                token = prefix + "a" * 24 + marker + "b" * 24
                with self.subTest(prefix=prefix, marker=marker):
                    self.assertIn("provider_api_key", scanner.findings(token))
                    self.assertNotIn(token, repr(scanner.findings(token)))

    def test_admitad_export_urls_and_named_codes_are_rejected(self):
        credential = "a1" * 16
        export = "https://export.admitad.com/en/webmaster/websites/REPLACE_ME/products/export/?user=REPLACE_ME&code="
        self.assertEqual(scanner.findings(export + credential), ["credential_url"])
        self.assertEqual(scanner.findings(export.replace("&code=", "&amp;code=") + credential), ["credential_url"])
        self.assertEqual(scanner.findings("ADMITAD_EXPORT_CODE=" + credential), ["credential_assignment"])
        self.assertIn("credential_url", scanner.findings(export + "a1example" * 5))

    def test_named_literal_credentials_are_detected_in_source_json_and_env(self):
        credential = "a1B2" * 10
        for statement in (
            'AI_API_KEY="{}"',
            'deepseek_api_key: str = "{}"',
            '{{"client_secret": "{}"}}',
            'JWT_SECRET={}',
        ):
            with self.subTest(statement=statement):
                self.assertIn("credential_assignment", scanner.findings(statement.format(credential)))

    def test_short_quoted_password_and_lowercase_passphrase_are_rejected(self):
        # Construct every positive fixture at runtime so the repository itself
        # contains no credential assignment with a secret-shaped literal.
        short_fixture = "mango" + "!" + "42"
        passphrase = " ".join(("correct", "horse", "battery", "staple"))
        short_api_code = "a1" * 5
        for name, value in (("password", short_fixture), ("password", passphrase), ("password", "p" + "!"), ("ADMITAD_EXPORT_CODE", short_api_code)):
            line = name + "=" + repr(value)
            with self.subTest(name=name):
                self.assertIn("credential_assignment", scanner.findings(line))

    def test_unquoted_environment_literals_are_checked_without_entropy_gate(self):
        name = "AI_API_" + "KEY"
        value = "lowercase" + "passphrase"
        self.assertIn("credential_assignment", scanner.findings(name + "=" + value, "backend/.env.example"))

    def test_other_provider_and_private_key_shapes_are_rejected(self):
        fixtures = (
            ("ghp_" + "a" * 36, "github_token"),
            ("github_pat_" + "a" * 40, "github_token"),
            ("xoxb-" + "1234567890-" * 3, "slack_token"),
            ("AKIA" + "A" * 16, "aws_access_key"),
            ("AIza" + "a" * 35, "google_api_key"),
            ("-----BEGIN " + "OPENSSH PRIVATE KEY-----", "private_key"),
        )
        for value, kind in fixtures:
            with self.subTest(kind=kind):
                self.assertEqual(scanner.findings(value), [kind])

    def test_explicit_placeholders_and_public_query_codes_are_allowed(self):
        fixtures = (
            "AI_API_KEY=REPLACE_ME",
            "ADMITAD_EXPORT_CODE=YOUR_EXPORT_CODE",
            "JWT_SECRET=SYNTHETIC_TEST_FIXTURE",
            "ADMIN_TOKEN=replace-with-random-token",
            'JWT_SECRET="isolated-fixture"',
            "https://example.invalid/?code=REPLACE_ME",
            "https://example.invalid/?code=%7BEXPORT_CODE%7D",
            "https://example.invalid/?code=123",
            "postgresql://REPLACE_ME:${DB_PASSWORD}@db/REPLACE_ME",
            'postgresql://REPLACE_ME:{env["DB_PASSWORD"]}@db/REPLACE_ME',
            "https://example.invalid/?code=secret-test-value",
            'token="secret-test-value"',
            'password=""',
            'ADMITAD_EXPORT_CODE=""',
        )
        for fixture in fixtures:
            with self.subTest(fixture=fixture):
                self.assertEqual(scanner.findings(fixture), [])

    def test_code_references_and_fixture_tokens_are_allowed(self):
        for statement in (
            "token = await storage.readToken()",
            "refresh_token: refresh!",
            "password: password!",
            'token: str(body["access_token"])',
            'JWT_SECRET=os.environ["JWT_SECRET"]',
            'ADMITAD_EXPORT_CODE="test-only-feed-code"',
            'token="SYNTHETIC_SESSION_TOKEN"',
            'refresh_token="SYNTHETIC_ROTATING_REFRESH"',
            'refresh_token: payload["refresh_token"]',
            'reasons_by_size_token: {"M": 3}',
            'availability_by_size_token: ["M", "L"]',
        ):
            with self.subTest(statement=statement):
                self.assertEqual(scanner.findings(statement), [])

    def test_shell_expansion_is_a_reference_instead_of_a_credential_literal(self):
        statement = "ADMIN_" + 'TOKEN="prefix $2 suffix"'
        self.assertEqual(scanner.findings(statement, "deploy/example.sh"), [])

    def test_placeholder_before_real_credential_does_not_hide_it(self):
        line = "https://example.invalid/?code=REPLACE_ME&access_token=" + "a1" * 16
        self.assertIn("credential_url", scanner.findings(line))

    def test_placeholder_exemptions_apply_to_the_complete_value_only(self):
        for value in ("a1" * 12 + "example", "a1" * 12 + "REPLACE_ME", "private-id" + "a1" * 12):
            self.assertFalse(scanner.is_placeholder(value))
            self.assertIn("credential_assignment", scanner.findings("AI_API_KEY=" + value))

    def test_tree_scans_tracked_and_nonignored_new_files_with_null_delimiters(self):
        with tempfile.TemporaryDirectory(prefix="modish-guard-") as folder:
            root = Path(folder)
            for name in ("tracked.py", "new source.py"):
                (root / name).write_text("sk-" + "a" * 32, encoding="utf-8")
            with patch.object(scanner, "git", return_value=b"tracked.py\0new source.py\0tracked.py\0") as git:
                results = list(scanner.scan_tree(root))
            self.assertEqual({result["path"] for result in results}, {"tracked.py", "new source.py"})
            self.assertEqual(len(results), 2)
            self.assertEqual(git.call_args.args, (root, "ls-files", "--cached", "--others", "--exclude-standard", "-z"))

    def test_staged_credential_is_caught_with_a_clean_working_copy(self):
        with tempfile.TemporaryDirectory(prefix="modish-guard-") as folder:
            root = Path(folder)
            (root / "config.py").write_text("AI_API_KEY=REPLACE_ME", encoding="utf-8")
            staged = ("sk-" + "a" * 32).encode()

            def fake_git(_root, *args):
                if args[0] == "ls-files":
                    return b"config.py\0"
                if args[0] == "diff":
                    return b"config.py\0"
                if args == ("show", ":config.py"):
                    return staged
                self.fail("Unexpected Git command")

            with patch.object(scanner, "git", side_effect=fake_git):
                self.assertEqual(list(scanner.scan_tree(root)), [])
                results = list(scanner.scan_staged(root))
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["commit"], "INDEX")
            self.assertEqual(results[0]["type"], "provider_api_key")

    def test_private_local_files_and_archives_are_never_opened(self):
        paths = b"private.env\0backend/.env\0deploy/prod.env\0.ssh/id_ed25519\0secret.key\0"
        with patch.object(scanner, "git", return_value=paths), patch.object(Path, "read_bytes", side_effect=AssertionError("Unexpected file read")):
            results = list(scanner.scan_tree(Path.cwd()))
        self.assertEqual({result["path"] for result in results}, {"private.env", "backend/.env", "deploy/prod.env", ".ssh/id_ed25519", "secret.key"})
        self.assertTrue(all(result["type"] == "private_credential_file" for result in results))
        with patch.object(scanner, "git", return_value=b"private.env\0") as git:
            results = list(scanner.scan_staged(Path.cwd()))
        self.assertEqual(git.call_count, 1)
        self.assertEqual(results[0]["type"], "private_credential_file")

    def test_excluded_source_paths_fail_closed_without_opening_their_contents(self):
        paths = b"archive/new.py\0archives/source.py\0.tmp/staged.py\0tmp/source.py\0"
        for scan in (scanner.scan_tree, scanner.scan_staged):
            with self.subTest(scope=scan.__name__), patch.object(scanner, "git", return_value=paths) as git, patch.object(Path, "read_bytes", side_effect=AssertionError("Unexpected artifact read")):
                results = list(scan(Path.cwd()))
            self.assertEqual(git.call_count, 1)
            self.assertEqual(len(results), 4)
            self.assertTrue(all(result["type"] == "unscanned_tracked_artifact" for result in results))

    def test_symlinks_and_binary_files_are_not_followed_as_text(self):
        with patch.object(scanner, "git", return_value=b"linked.py\0"), patch.object(Path, "is_symlink", return_value=True), patch.object(Path, "read_bytes", side_effect=AssertionError("Unexpected file read")):
            self.assertEqual(list(scanner.scan_tree(Path.cwd())), [])
        self.assertEqual(list(scanner.scan_data(b"\0" + ("sk-" + "a" * 32).encode(), "image.bin", "WORKTREE")), [])

    def test_cli_returns_failure_and_never_prints_matched_content(self):
        credential = "sk-" + "a" * 32
        data = list(scanner.scan_data(credential.encode(), "config.py", "INDEX"))
        output = io.StringIO()
        with patch("sys.argv", ["scan_secrets.py"]), patch.object(scanner, "git", return_value=str(Path.cwd()).encode()), patch.object(scanner, "scan_tree", return_value=iter(data)), patch.object(scanner, "scan_staged", return_value=iter(())), redirect_stdout(output):
            status = scanner.main()
        self.assertEqual(status, 1)
        self.assertNotIn(credential, output.getvalue())
        self.assertIn('"type": "provider_api_key"', output.getvalue())
        self.assertEqual(set(data[0]), {"commit", "path", "line", "type"})

    def test_git_or_read_errors_fail_closed_without_echoing_error_details(self):
        for error in (scanner.ScanError("diagnostic-private-content"), OSError("diagnostic-private-content")):
            output = io.StringIO()
            with patch("sys.argv", ["scan_secrets.py"]), patch.object(scanner, "git", side_effect=error), redirect_stdout(output):
                status = scanner.main()
            self.assertEqual(status, 2)
            self.assertNotIn("diagnostic-private-content", output.getvalue())

    def test_unreadable_scan_input_fails_closed_without_echoing_exception(self):
        output = io.StringIO()
        with patch("sys.argv", ["scan_secrets.py"]), patch.object(scanner, "git", return_value=str(Path.cwd()).encode()), patch.object(scanner, "scan_tree", side_effect=OSError("diagnostic-private-content")), redirect_stdout(output):
            status = scanner.main()
        self.assertEqual(status, 2)
        self.assertNotIn("diagnostic-private-content", output.getvalue())
