import os
import unittest
from unittest.mock import patch

from app import config
from app.integrations.admitad.source_presets import admitad_csv_feed_url


class SecureConfigTests(unittest.TestCase):
  def test_disabled_admitad_needs_no_credentials(self):
    with patch.dict(os.environ, {}, clear=True):
      self.assertIsNone(config.get_admitad_export_config())

  def test_enabled_admitad_rejects_placeholder_without_echo(self):
    with patch.dict(os.environ, {
      "ADMITAD_ENABLED": "true", "ADMITAD_WEBSITE_ID": "123",
      "ADMITAD_EXPORT_USER": "test-user", "ADMITAD_EXPORT_CODE": "CHANGE_ME_PRIVATE",
    }, clear=True):
      with self.assertRaises(ValueError) as caught:
        admitad_csv_feed_url("21738")
      self.assertNotIn("CHANGE_ME_PRIVATE", str(caught.exception))

  def test_export_url_encodes_credentials_and_uses_https(self):
    from urllib.parse import parse_qs, urlsplit
    with patch.dict(os.environ, {
      "ADMITAD_ENABLED": "true", "ADMITAD_WEBSITE_ID": "123",
      "ADMITAD_EXPORT_USER": "synthetic-user", "ADMITAD_EXPORT_CODE": "synthetic&test=value",
    }, clear=True):
      url = urlsplit(admitad_csv_feed_url("21738"))
      self.assertEqual(url.scheme, "https")
      self.assertEqual(url.hostname, "export.admitad.com")
      self.assertEqual(parse_qs(url.query)["code"], ["synthetic&test=value"])
      self.assertEqual(parse_qs(url.query)["feed_id"], ["21738"])

  def test_production_rejects_placeholder_jwt(self):
    with patch.dict(os.environ, {"APP_ENV": "production", "JWT_SECRET": "change_me_" * 8}, clear=True):
      with self.assertRaises(ValueError):
        config.get_jwt_secret()

  def test_missing_optional_ai_does_not_block_database_config(self):
    with patch.dict(os.environ, {"DATABASE_URL": "sqlite:///:memory:"}, clear=True):
      self.assertEqual(config.get_database_url(), "sqlite:///:memory:")

  def test_log_redaction_includes_exception_and_query(self):
    import io
    import logging
    from app.log_redaction import CredentialRedactionFilter
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(CredentialRedactionFilter())
    logger = logging.Logger("redaction-test")
    logger.addHandler(handler)
    secret = "private-" + "value"
    try:
      raise RuntimeError("https://example.invalid/?code=" + secret)
    except RuntimeError:
      logger.exception("Feed request %s", "https://example.invalid/?code=" + secret)
    self.assertNotIn(secret, stream.getvalue())
    self.assertIn("[REDACTED]", stream.getvalue())

  def test_disabled_bootstrap_does_not_modify_database(self):
    from unittest.mock import Mock
    from app.services.catalog.admitad_bootstrap import bootstrap_admitad_csv_sources
    db = Mock()
    with patch.dict(os.environ, {}, clear=True):
      with self.assertRaises(ValueError):
        bootstrap_admitad_csv_sources(db)
    self.assertEqual(db.mock_calls, [])

  def test_disabled_admitad_cannot_download_previously_stored_url(self):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from app.services.catalog.feed_import_service import sync_partner_feed
    source = SimpleNamespace(id="source", network="admitad", feed_url="https://example.invalid/feed")
    with patch.dict(os.environ, {}, clear=True), patch(
      "app.services.catalog.feed_import_service._download_feed_body"
    ) as download:
      run = sync_partner_feed(Mock(), source=source)
    download.assert_not_called()
    self.assertEqual(run.status, "error")
    self.assertEqual(run.error_message, "Admitad export is disabled")
