"""Catalog P0: replayable source records, honest facts and quarantine lifecycle."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import importlib.util
import unittest
from unittest.mock import patch

from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.integrations.admitad.feed_parser import parse_yml_catalog_xml
from app.models import CatalogRecord, Product, ProductSource
from app.api.catalog_admin import admin_catalog_quality, admin_catalog_quarantine, admin_catalog_reprocess, ReprocessCatalogBody
from app.services.catalog.catalog_records import prepare_record, quality_summary, reprocess_records, store_record
from app.services.catalog.catalog_quality import product_is_feed_eligible
from app.services.catalog.feed_import_service import _ingest_feed_rows, sync_partner_feed_from_text
from app.services.catalog.feed_row_mapper import _int_price
from app.services.catalog.merchant_adapters import get_adapter
from app.services.catalog.versions import NORMALIZATION_VERSION


def offer(**changes):
    return {"id": "p1", "name": "Женская рубашка", "category": "Женское/Рубашки",
        "gender": "женский", "price": "3999.00", "oldprice": "5999.00", "currency": "RUR",
        "sizes": "M,L", "available": True, "picture": "https://shop.example/photo.jpg",
        "url": "https://shop.example/product/p1", "param_состав": "100% хлопок", **changes}


class CatalogFoundationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, autoflush=False)()
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        self.source = ProductSource(id="source", code="baon", name="BAON", network="direct")
        self.db.add(self.source)
        self.db.commit()
        self.now = datetime.now(timezone.utc)

    def store(self, row):
        return store_record(self.db, self.source, row, self.now)

    def test_prices_keep_decimal_units_and_reject_invalid_values(self):
        for value, expected in [("3999.00", 3999), ("1 299,50", 1300), ("1,299.00", 1299),
                                ("1.299,00", 1299), (3999.0, 3999), ("-99", None),
                                ("NaN", None), ("Infinity", None), (True, None), ("call", None)]:
            with self.subTest(value=value):
                self.assertEqual(_int_price(value), expected)

    def test_source_record_is_unchanged_and_canonical_has_versions(self):
        raw = offer()
        original = deepcopy(raw)
        record, action = self.store(raw)
        self.assertEqual(raw, original)
        self.assertEqual(record.raw_json, original)
        self.assertEqual(action, "created")
        self.assertEqual(record.status, "passed")
        self.assertEqual(record.canonical_json["price"], 3999)
        self.assertEqual(record.canonical_json["currency"], "RUB")
        self.assertEqual(record.canonical_json["material"], "100% хлопок")
        self.assertEqual(record.canonical_json["brand"], "BAON")
        for field in ("schema_version", "taxonomy_version", "enrichment_version"):
            self.assertTrue(record.canonical_json[field])
        self.assertEqual(record.canonical_json["delivery_eligibility"], "unknown")
        self.assertEqual(record.normalization_version, NORMALIZATION_VERSION)

    def test_yml_preserves_repeated_sizes_and_missing_identity(self):
        rows = parse_yml_catalog_xml('<yml_catalog><shop><offers><offer id="1" available="true"><name>Рубашка</name><param name="Размер" unit="RU">42</param><param name="Размер" unit="RU">44</param></offer><offer><name>Без id</name></offer></offers></shop></yml_catalog>')
        self.assertEqual(len(rows), 2)
        n = get_adapter("befree").normalize(rows[0], self.source)
        self.assertEqual(n.sizes, ["42", "44"])
        self.assertEqual(rows[0]["param_размер_unit"], "RU")
        record, _ = self.store(rows[1])
        self.assertEqual(record.reasons, ["invalid_identity"])

    def test_false_zero_unknown_and_conflicting_stock_never_mean_available(self):
        for value, status in [(False, "unavailable"), (0, "unavailable"), ("false", "unavailable"),
                              (None, "quarantined"), ("preorder", "quarantined")]:
            with self.subTest(value=value):
                record, _ = self.store(offer(available=value))
                self.assertEqual(record.status, status)
                if record.product_id:
                    p = self.db.get(Product, record.product_id)
                    self.assertFalse(p.is_available)
                    self.assertFalse(product_is_feed_eligible(p))
        record, _ = self.store(offer(available=True, stock=0))
        self.assertEqual(record.status, "unavailable")

    def test_missing_currency_and_sizes_not_invented(self):
        record, _ = self.store(offer(currency="", sizes=""))
        self.assertEqual(record.status, "quarantined")
        self.assertIn("missing_size", record.reasons)
        self.assertIn("unsupported_currency", record.reasons)
        self.assertEqual(record.canonical_json["currency"], "")

    def test_unknown_and_child_sizes_quarantined(self):
        for size in ("БЕЗРАЗМЕРА", "70X70CM", "122-128", "MAYBE", "M,UNKNOWN"):
            with self.subTest(size=size):
                record, _ = self.store(offer(sizes=size))
                self.assertEqual(record.status, "quarantined")
                self.assertIsNone(record.product_id)

    def test_letter_aliases_and_numeric_size_system_are_honest(self):
        record, _ = self.store(offer(sizes="2XS,2XL,3XL"))
        self.assertEqual(record.canonical_json["available_sizes"], ["XXS", "XXL", "XXXL"])
        self.assertEqual(record.status, "passed")
        record, _ = self.store(offer(sizes="42"))
        self.assertIsNone(record.canonical_json["size_system"])
        record, _ = self.store(offer(sizes="42", param_размер_unit="RU"))
        self.assertEqual(record.canonical_json["size_system"], "RU")

    def test_adult_baby_doll_silhouette_is_not_childrenswear(self):
        record, _ = self.store(offer(name="Платье baby-doll", category="Платья", description="Женское платье baby doll"))
        self.assertEqual(record.status, "passed")
        record, _ = self.store(offer(name="Baby doll dress for girls", category="Платья"))
        self.assertIn("childrenswear", record.reasons)

    def test_real_feed_cyrillic_letter_m_is_not_an_unknown_size(self):
        record, _ = self.store(offer(sizes="XS,S,М,L,ХL"))
        self.assertEqual(record.status, "passed")
        self.assertEqual(record.canonical_json["available_sizes"], ["XS", "S", "M", "L", "XL"])

    def test_collection_and_vendor_model_categories_use_sold_item_noun(self):
        for title, category, expected in [
            ("Платье с рукавами крылышками", "vendor.model", "платья"),
            ("Джинсы Straight Denim FABLE", "DROP 002", "джинсы"),
            ("Пальто укороченное двубортное", "Верхняя одежда", "верхний_слой"),
            ("Зауженные брюки со стрелками", "Office", "брюки"),
        ]:
            record, _ = self.store(offer(name=title, category=category))
            self.assertEqual(record.canonical_json["category"], expected)
            self.assertEqual(record.status, "passed")
        for title in ("Ремень для платья", "Подушка с рисунком рубашки"):
            record, _ = self.store(offer(name=title, category="Подарки"))
            self.assertIn("unsupported_category", record.reasons)

    def test_title_combined_size_is_not_asserted_as_single_m(self):
        record, _ = self.store(offer(name="Джемпер вязаный M/L, лавандовый", sizes=""))
        self.assertEqual(record.canonical_json["available_sizes"], ["M/L"])
        self.assertNotIn("M", record.canonical_json["available_sizes"])

    def test_replay_never_refreshes_old_stock_observation(self):
        observed = self.now - timedelta(days=4)
        record, _ = store_record(self.db, self.source, offer(), observed)
        pid = record.product_id
        record, _ = store_record(self.db, self.source, offer(), self.now, last_seen_at=observed)
        self.assertEqual(record.status, "quarantined")
        self.assertIn("stale_availability", record.reasons)
        self.assertEqual(record.last_seen_at, observed)
        self.assertEqual(record.canonical_json["availability_observed_at"], observed.isoformat())
        self.assertFalse(self.db.get(Product, pid).is_active)
        result = reprocess_records(self.db, self.source, dry_run=True)
        self.assertEqual(result["reasons"], {"stale_availability": 1})
        record, _ = store_record(self.db, self.source, offer(), self.now)
        self.assertEqual(record.product_id, pid)
        self.assertEqual(record.status, "passed")

    def test_stock_clock_boundary_and_future_timestamp(self):
        for age, passed in [(timedelta(hours=72), True), (timedelta(hours=72,seconds=1), False),
                            (timedelta(minutes=-6), False)]:
            *_, decision = prepare_record(offer(), self.source, self.now, observed_at=self.now-age)
            self.assertEqual(decision.status == 'passed', passed)

    def test_non_fashion_children_and_invalid_links_rejected(self):
        for changes, reason in [({"name": "Подушка", "category": "Подушки"}, "unsupported_category"),
                                ({"name": "Футболка для девочек"}, "childrenswear"),
                                ({"picture": "file:///private.jpg"}, "invalid_image_url"),
                                ({"url": "http://localhost/product"}, "invalid_product_url"),
                                ({"url": "https://admitad.com/g/broken/"}, "invalid_product_url")]:
            with self.subTest(reason=reason):
                record, _ = self.store(offer(**changes))
                self.assertIn(reason, record.reasons)

    def test_bad_update_hides_previous_product_without_claiming_it_disappeared(self):
        record, _ = self.store(offer())
        pid = record.product_id
        record, _ = self.store(offer(picture=""))
        self.assertEqual(record.product_id, pid)
        self.assertEqual(record.status, "quarantined")
        p = self.db.get(Product, pid)
        self.assertFalse(p.is_active)
        self.assertFalse(p.is_deleted_from_feed)
        self.assertFalse(product_is_feed_eligible(p))
        self.assertEqual(p.image_url, "https://shop.example/photo.jpg")

    def test_fixed_source_row_restores_same_product(self):
        record, _ = self.store(offer())
        pid, rid = record.product_id, record.id
        self.store(offer(available=None))
        record, _ = self.store(offer(price="2999.00"))
        self.assertEqual((record.product_id, record.id), (pid, rid))
        self.assertTrue(self.db.get(Product, pid).is_active)
        self.assertEqual(self.db.get(Product, pid).price, 2999)

    def test_duplicate_ids_are_idempotent_and_merchants_are_separate(self):
        _ingest_feed_rows(self.db, source=self.source, rows=[offer(), offer(price=2500)])
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Product)), 1)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(CatalogRecord)), 1)
        self.assertEqual(self.db.scalar(select(Product)).price, 2500)
        source2 = ProductSource(id="source2", code="sela", name="Sela", network="direct")
        self.db.add(source2)
        self.db.flush()
        store_record(self.db, source2, offer(), self.now)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Product)), 2)

    def test_unidentifiable_rows_remain_replayable_without_truncation_collision(self):
        for identifier in ("", "x" * 129):
            record, _ = self.store(offer(id=identifier))
            again, _ = self.store(offer(id=identifier))
            self.assertEqual(record.id, again.id)
            self.assertEqual(record.reasons, ["invalid_identity"])
            self.assertEqual(record.raw_json["id"], identifier)
            self.assertIsNone(record.product_id)

    def test_replay_dry_run_does_not_write_or_fetch_a_feed(self):
        record, _ = self.store(offer())
        record.normalization_version = "old"
        self.db.commit()
        with patch("app.services.catalog.feed_import_service._download_feed_body") as download:
            result = reprocess_records(self.db, self.source)
            self.assertEqual(result["counts"], {"passed": 1})
            self.assertEqual(record.normalization_version, "old")
            reprocess_records(self.db, self.source, dry_run=False)
            self.assertEqual(record.normalization_version, NORMALIZATION_VERSION)
            download.assert_not_called()

    def test_missing_products_are_not_resurrected_by_replay(self):
        _ingest_feed_rows(self.db, source=self.source, rows=[offer(), offer(id="p2")])
        _ingest_feed_rows(self.db, source=self.source, rows=[offer(id="p2")])
        result = reprocess_records(self.db, self.source, dry_run=False)
        self.assertEqual(result["processed"], 1)
        p = self.db.scalar(select(Product).where(Product.external_id == "p1"))
        self.assertTrue(p.is_deleted_from_feed)
        self.assertFalse(p.is_active)
        self.assertEqual(self.db.scalar(select(CatalogRecord).where(CatalogRecord.external_id == "p1")).status, "missing")

    def test_entire_batch_rolls_back_on_unexpected_failure(self):
        body = 'id;name;price;currency;available;size;gender;category;picture;url\np1;Рубашка;1000;RUB;true;M;женский;Рубашки;https://shop.example/a;https://shop.example/p\np2;Рубашка;1000;RUB;true;M;женский;Рубашки;https://shop.example/a;https://shop.example/p\n'
        original_store = store_record
        def fail_second(db, source, row, timestamp):
            if row["id"] == "p2":
                raise RuntimeError("synthetic failure")
            return original_store(db, source, row, timestamp)
        with patch("app.services.catalog.feed_import_service.store_record", side_effect=fail_second):
            run = sync_partner_feed_from_text(self.db, source=self.source, body=body, parser_url_hint="feed.csv")
        self.assertEqual(run.status, "error")
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Product)), 0)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(CatalogRecord)), 0)

    def test_quality_summary_and_admin_routes_are_protected(self):
        self.store(offer())
        self.store(offer(id="bad", sizes=""))
        summary = quality_summary(self.db)
        self.assertEqual(summary["counts"], {"passed": 1, "quarantined": 1})
        self.assertEqual(summary["quarantine_rate"], 0.5)
        with patch.dict("os.environ", {"ADMIN_TOKEN": "synthetic"}):
            for route, kwargs in [(admin_catalog_quality, {}), (admin_catalog_quarantine, {}),
                (admin_catalog_reprocess, {"source_id": "source", "payload": ReprocessCatalogBody()})]:
                with self.assertRaises(HTTPException):
                    route(db=self.db, authorization="Bearer wrong", **kwargs)
            result = admin_catalog_quarantine(db=self.db, authorization="Bearer synthetic")
            self.assertEqual(len(result["items"]), 1)
            self.assertNotIn("raw_json", result["items"][0])

    def test_replay_cursor_covers_each_record_once(self):
        for i in range(5):
            self.store(offer(id=str(i)))
        cursor, total = None, 0
        while True:
            result = reprocess_records(self.db, self.source, limit=2, after_id=cursor)
            total += result["processed"]
            if not result["has_more"]:
                break
            cursor = result["next_after_id"]
        self.assertEqual(total, 5)

    def test_adapters_never_infer_stock_or_gender_from_merchant(self):
        for merchant in ("befree", "sela", "baon", "unknown"):
            source = ProductSource(id="test", code=merchant, name=merchant, network="direct")
            n = get_adapter(merchant).normalize(offer(name="Рубашка", category="Рубашки", gender="", available=None), source)
            self.assertIsNone(n.gender_target)
            self.assertEqual(n.availability_status, "unknown")

    def test_new_migration_preserves_existing_products(self):
        record, _ = self.store(offer())
        self.db.commit()
        pid = record.product_id
        self.db.close()
        CatalogRecord.__table__.drop(self.engine)
        path = Path(__file__).resolve().parents[1] / "alembic/versions/20260921_0022_catalog_records.py"
        spec = importlib.util.spec_from_file_location("catalog_migration", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        with self.engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                self.assertIn("catalog_records", inspect(connection).get_table_names())
                self.assertEqual(connection.scalar(select(Product.id)), pid)
                migration.downgrade()
                self.assertNotIn("catalog_records", inspect(connection).get_table_names())
                self.assertEqual(connection.scalar(select(Product.id)), pid)


if __name__ == "__main__":
    unittest.main()
