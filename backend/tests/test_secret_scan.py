import importlib.util
from pathlib import Path
import unittest


class SecretScanTests(unittest.TestCase):
  def test_scanner_returns_only_types_and_allows_placeholders(self):
    path = Path(__file__).resolve().parents[2] / "scripts" / "scan_secrets.py"
    spec = importlib.util.spec_from_file_location("secret_scan", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    token = "sk-" + "a" * 32
    self.assertEqual(module.findings("API_KEY=" + token), ["provider_api_key"])
    self.assertEqual(module.findings("https://example.invalid/?code=" + "a" * 10), ["credential_url"])
    self.assertEqual(module.findings("AI_API_KEY=REPLACE_ME"), [])
    self.assertNotIn(token, repr(module.findings(token)))
