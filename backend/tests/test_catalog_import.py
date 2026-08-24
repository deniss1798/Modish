"""Unit tests for Befree YML catalog import pipeline."""
from __future__ import annotations

import unittest
from pathlib import Path

from app.integrations.admitad.feed_parser import parse_yml_catalog_xml
from app.models import ProductSource
from app.services.catalog.feed_row_mapper import row_to_normalized

SAMPLE = Path(__file__).resolve().parents[1] / "test_feeds" / "befree_sample.xml"


class BefreeImportTests(unittest.TestCase):
  def setUp(self) -> None:
    self.source = ProductSource(
      id="src-test",
      code="befree",
      name="Befree",
      network="direct",
      feed_url="https://example.com/feed.xml",
    )
    self.xml = SAMPLE.read_text(encoding="utf-8")

  def test_parse_yml_offers_count(self) -> None:
    rows = parse_yml_catalog_xml(self.xml)
    self.assertEqual(len(rows), 3)

  def test_row_maps_group_id_and_offer_id(self) -> None:
    rows = parse_yml_catalog_xml(self.xml)
    n = row_to_normalized(rows[0], self.source)
    assert n is not None
    self.assertEqual(n.external_id, "1001")
    self.assertEqual(n.group_id, "G-100")
    self.assertEqual(n.price, 2799)
    self.assertEqual(n.old_price, 3999)
    self.assertEqual(len(n.image_urls), 2)
    self.assertTrue(n.image_url.startswith("https://"))
    self.assertIn("M", n.sizes)
    # цвета теперь приводятся к каноническим: «бежевый» → cream
    self.assertIn("cream", n.colors)
    self.assertEqual(n.gender_target, "womenswear")
    self.assertEqual(n.category_name, "Куртки")

  def test_collection_id_fallback_as_group_id(self) -> None:
    rows = parse_yml_catalog_xml(self.xml)
    n = row_to_normalized(rows[1], self.source)
    assert n is not None
    self.assertEqual(n.external_id, "1002")
    self.assertEqual(n.group_id, "COL-55")
    self.assertTrue(n.image_url.startswith("https://"))

  def test_missing_picture_row_still_parses(self) -> None:
    rows = parse_yml_catalog_xml(self.xml)
    n = row_to_normalized(rows[2], self.source)
    assert n is not None
    self.assertEqual(n.external_id, "bad-no-picture")
    self.assertEqual(n.image_url, "")


if __name__ == "__main__":
  unittest.main()
