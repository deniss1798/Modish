"""Admitad CSV (FABLE / Aim Clo) — param, format=csv, clothing rules."""
from __future__ import annotations

import unittest

from app.integrations.admitad.feed_parser import parse_admitad_csv
from app.integrations.admitad.source_presets import ADMITAD_CSV_SOURCES
from app.models import ProductSource
from app.services.catalog.feed_import_service import _detect_parser
from app.services.catalog.feed_row_mapper import row_to_normalized
from app.services.catalog.rule_filters import product_passes_source_rules


SAMPLE_FABLE_ROW = (
  "available;categoryId;currencyId;description;id;model;modified_time;name;oldprice;param;picture;price;type;typePrefix;url;vendor\n"
  "true;Мужское/Футболки;RUR;desc;1001;DROP;1;Футболка Test;5990;Цвет:черный|Размер:M|Размер:L;"
  "https://example.com/a.jpg;4792;vendor.model;Футболка;https://example.com/p;FABLE\n"
  "true;Аксессуары;RUR;bag;2002;DROP;1;Сумка;9990;Цвет:черный|Размер:OS;"
  "https://example.com/b.jpg;8000;vendor.model;Сумка;https://example.com/bag;FABLE\n"
)

SAMPLE_AIMCLO_ROW = (
  "available;categoryId;currencyId;description;id;identifier_exists;modified_time;name;oldprice;param;picture;price;type;url;vendor\n"
  "true;Главная/Шорты;RUB;desc;3001;no;1;Шорты бермуды XS, хаки;3999.00;;"
  "http://example.com/j.jpg;3999.00;;https://example.com/j;\n"
)


class AdmitadCsvImportTests(unittest.TestCase):
  def test_presets_contain_all_admitad_shops(self) -> None:
    codes = {p.code for p in ADMITAD_CSV_SOURCES}
    self.assertEqual(
      codes,
      {
        "fable",
        "aimclo",
        "sportmaster",
        "shoppinglive",
        "postmeridiem",
        "baon",
        "mongolshop",
        "serginnetti",
      },
    )
    aim = next(p for p in ADMITAD_CSV_SOURCES if p.code == "aimclo")
    self.assertIn("feed_id=21738", aim.feed_url)
    sm = next(p for p in ADMITAD_CSV_SOURCES if p.code == "sportmaster")
    self.assertIn("feed_id=26327", sm.feed_url)

  def test_param_comma_separated_sizes(self) -> None:
    csv = (
      "available;categoryId;id;name;param;picture;price;url;vendor\n"
      "true;Платья;1;Платье;Цвет:Голубой|Размер:XS,S,M,L;"
      "https://example.com/p.jpg;1000;https://example.com/x;PM\n"
    )
    rows = parse_admitad_csv(csv)
    self.assertEqual(rows[0]["sizes"], "XS|S|M|L")
    for p in ADMITAD_CSV_SOURCES:
      self.assertIn("format=csv", p.feed_url)

  def test_detect_parser_format_csv_query(self) -> None:
    url = ADMITAD_CSV_SOURCES[0].feed_url
    rows = _detect_parser(url, SAMPLE_FABLE_ROW)
    self.assertEqual(len(rows), 2)
    self.assertIn("sizes", rows[0])

  def test_param_expands_sizes_and_color(self) -> None:
    rows = parse_admitad_csv(SAMPLE_FABLE_ROW)
    self.assertEqual(len(rows), 2)
    self.assertIn("M", rows[0].get("sizes", ""))
    self.assertIn("L", rows[0].get("sizes", ""))
    self.assertEqual(rows[0].get("param_цвет"), "черный")

  def test_aimclo_sizes_from_title(self) -> None:
    rows = parse_admitad_csv(SAMPLE_AIMCLO_ROW)
    src = ProductSource(id="s2", code="aimclo", name="Aim Clo", network="admitad", feed_url="x")
    n = row_to_normalized(rows[0], src)
    assert n is not None
    self.assertTrue(n.sizes)

  def test_fable_row_maps_gender_from_category(self) -> None:
    rows = parse_admitad_csv(SAMPLE_FABLE_ROW)
    src = ProductSource(id="s1", code="fable", name="FABLE", network="admitad", feed_url="x")
    n = row_to_normalized(rows[0], src)
    assert n is not None
    self.assertEqual(n.gender_target, "menswear")
    self.assertIn("M", n.sizes)

  def test_blocked_accessories_category(self) -> None:
    rows = parse_admitad_csv(SAMPLE_FABLE_ROW)
    src = ProductSource(id="s1", code="fable", name="FABLE", network="admitad", feed_url="x")
    from app.models import Product

    n = row_to_normalized(rows[1], src)
    assert n is not None
    p = Product(
      id="p1",
      source="fable",
      source_id=src.id,
      category=n.category,
      title=n.title,
      brand=n.brand or "",
      price=n.price,
      image_url=n.image_url or "https://x.jpg",
      affiliate_url="https://aff",
      available_sizes=n.sizes,
    )
    rules = {
      src.id: [
        type("R", (), {"rule_type": "blocked_category", "rule_value": "аксессуар", "is_active": 1})()
      ]
    }
    self.assertFalse(product_passes_source_rules(p, rules))


if __name__ == "__main__":
  unittest.main()
