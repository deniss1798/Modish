"""Regression cases for lost decimal tokens and already-declared size evidence."""
from datetime import datetime, timezone
import unittest

from app.integrations.admitad.feed_parser import parse_admitad_csv, parse_yml_catalog_xml
from app.models import Product, ProductSource
from app.services.catalog.catalog_records import prepare_record
from app.services.catalog.size_resolver import exact_size_match, explicit_size_system, resolve_product_sizes


class SizeEvidenceLossTests(unittest.TestCase):
    def setUp(self):
        self.source = ProductSource(id="synthetic", code="synthetic", name="Synthetic", network="direct")
        self.now = datetime.now(timezone.utc)

    def product_from_row(self, row):
        fields = {"name": "Женские туфли", "category": "обувь", "gender": "женский",
            "currency": "RUB", "price": "2000", "picture": "https://shop.example/shoe.jpg",
            "url": "https://shop.example/shoe", **row}
        *_, payload, canonical, decision = prepare_record(fields, self.source, self.now)
        self.assertEqual(decision.status, "passed")
        return Product(**payload), canonical

    def assert_half_size_only(self, product):
        self.assertTrue(exact_size_match(product, "7.5", requested_system="UK"))
        self.assertTrue(exact_size_match(product, "7,5", requested_system="UK"))
        for value in ("7", "5", "8.5"):
            self.assertFalse(exact_size_match(product, value, requested_system="UK"))
        self.assertFalse(exact_size_match(product, "7.5", requested_system="US"))

    def test_yml_scalar_decimal_comma_keeps_raw_token_and_exact_variant(self):
        for raw in ("7,5", "7, 5"):
            row = parse_yml_catalog_xml(f'<shop><offers><offer id="shoe" available="true"><param name="Размер" unit="UK">{raw}</param></offer></offers></shop>')[0]
            product, canonical = self.product_from_row(row)
            self.assertEqual(product.available_sizes, [raw.replace(" ", "")])
            self.assertEqual(canonical["available_sizes_detailed"][0]["raw_size"], raw)
            self.assertEqual(canonical["available_sizes_detailed"][0]["normalized_size"], "7.5")
            self.assert_half_size_only(product)

    def test_csv_scalar_decimal_comma_does_not_create_two_variants(self):
        row = parse_admitad_csv("id;available;param\nshoe;true;Размер:7,5|Size Unit:UK\n")[0]
        self.assertEqual(row["sizes"], "7,5")
        self.assertEqual(row["param_размер"], "7,5")
        product, _ = self.product_from_row(row)
        self.assert_half_size_only(product)

    def test_known_list_separators_and_letter_lists_are_preserved(self):
        for raw, expected in [("Размер:7,5|Размер:8,5", ["7,5", "8,5"]),
                ("Размер:7,5;8,5", ["7,5", "8,5"]), ("Размер:36,37,38", ["36", "37", "38"]),
                ("Размер:XS,S,M,L", ["XS", "S", "M", "L"])]:
            row = parse_admitad_csv(f'id;available;param\nshoe;true;"{raw}"\n')[0]
            self.assertEqual(row["sizes"], "|".join(expected))

    def test_ambiguous_comma_multivalue_is_not_promoted_to_integer_sizes(self):
        row = parse_admitad_csv("id;available;param\nshoe;true;Размер:7,5,8,5|Size Unit:UK\n")[0]
        self.assertEqual(row["sizes"], "7,5,8,5")
        product = Product(category="обувь", source="synthetic", available_sizes=[row["sizes"]], feed_raw_json=row)
        for value in ("7", "5", "8", "7.5", "8.5"):
            self.assertFalse(exact_size_match(product, value, requested_system="UK"))

    def test_missing_or_conflicting_system_still_cannot_confirm_half_size(self):
        for params in ("Размер:7,5", "Размер:7,5|Size Unit:UK|Size System:US"):
            row = parse_admitad_csv(f"id;available;param\nshoe;true;{params}\n")[0]
            self.assertEqual(row["sizes"], "7,5")
            product = Product(category="обувь", source="synthetic", available_sizes=["7,5"], feed_raw_json=row)
            for system in ("UK", "US"):
                self.assertFalse(exact_size_match(product, "7.5", requested_system=system))

    def test_legacy_split_decimal_is_blocked_by_preserved_original(self):
        product = Product(category="обувь", source="synthetic", available_sizes=["7", "5"],
            feed_raw_json={"param_размер": "7,5", "param_размер_unit": "UK"})
        for value in ("7", "5"):
            self.assertFalse(exact_size_match(product, value, requested_system="UK"))
        self.assertEqual({s.reason for s in resolve_product_sizes(product)}, {"ambiguous_compound_size"})

    def test_stock_details_match_equivalent_decimal_spellings(self):
        product = Product(category="обувь", source="synthetic", available_sizes=["7.5"],
            feed_raw_json={"param_размер": "7,5", "param_размер_unit": "UK"},
            available_sizes_detailed=[{"raw_size": "7,5", "availability": "out_of_stock"}])
        self.assertFalse(exact_size_match(product, "7.5", requested_system="UK"))
        self.assertEqual(resolve_product_sizes(product)[0].availability, "unavailable")
        self.assertEqual(resolve_product_sizes(product)[0].raw_size, "7,5")

    def test_equivalent_stock_statuses_agree_but_negative_or_unknown_do_not(self):
        for states, expected in [(["available", "in_stock"], "available"),
                (["unavailable", "out_of_stock"], "unavailable"),
                (["available", "out_of_stock"], "unknown"),
                (["in_stock", "unknown"], "unknown")]:
            product = Product(category="обувь", source="synthetic", available_sizes=["38"],
                feed_raw_json={"size_system": "EU"},
                available_sizes_detailed=[{"size": "38", "availability": s} for s in states])
            self.assertEqual(resolve_product_sizes(product)[0].availability, expected)
            self.assertEqual(exact_size_match(product, "38", requested_system="EU"), expected == "available")

    def test_csv_param_size_system_alias_confirms_only_its_system(self):
        row = parse_admitad_csv("id;available;param\nshoe;true;Размер:38|Size System:EU\n")[0]
        self.assertEqual(explicit_size_system(row), "EU")
        product, _ = self.product_from_row(row)
        self.assertTrue(exact_size_match(product, "38", requested_system="EU"))
        self.assertFalse(exact_size_match(product, "38", requested_system="RU"))
        self.assertIsNone(explicit_size_system({**row, "size_system": "RU"}))

    def test_repeated_csv_system_fields_preserve_conflicts(self):
        for name in ("Size System", "Size Unit", "Размер unit"):
            row = parse_admitad_csv(f"id;available;param\nshoe;true;Размер:38|{name}:EU|{name}:UK\n")[0]
            self.assertIsNone(explicit_size_system(row))
            product = Product(category="обувь", source="synthetic", available_sizes=["38"], feed_raw_json=row)
            self.assertFalse(exact_size_match(product, "38", requested_system="UK"))
            self.assertEqual(resolve_product_sizes(product)[0].reason, "conflicting_size_system")
