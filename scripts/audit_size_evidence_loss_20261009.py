"""Bounded, offline reproductions for size evidence loss; never opens a DB/feed.

This is a research script, not a proposed production implementation. It reads
only checked-in aggregate reports and uses explicitly synthetic merchant rows.
An existing output file is never replaced.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.integrations.admitad.feed_parser import parse_admitad_csv, parse_yml_catalog_xml
from app.services.catalog.merchant_adapters import get_adapter
from app.services.catalog.product_normalizer import normalized_to_dict
from app.services.catalog.size_resolver import exact_size_match, resolve_product_sizes


def normalize(row):
    source = SimpleNamespace(code="synthetic_size_evidence_probe")
    n = get_adapter(source.code).normalize(row, source)
    assert n is not None
    return normalized_to_dict(n)


def result(row, wanted, system):
    payload = normalize(row)
    product = SimpleNamespace(**payload)
    return {
        "parsed_size_fields": {k: v for k, v in row.items() if "size" in k or "размер" in k},
        "available_sizes": payload["available_sizes"],
        "availability_status": payload["availability_status"],
        "size_system": payload["size_system"],
        "details": payload["available_sizes_detailed"],
        "matches": {value: exact_size_match(product, value, requested_system=system) for value in wanted},
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    baseline = {
        "id": "synthetic-shoe", "name": "Женские туфли", "category": "обувь",
        "available": "true", "sizes": "38", "size_system": "EU",
        "currency": "RUB", "gender": "женский", "price": "2000",
    }
    cases = {}
    cases["explicit_integer_control"] = result(baseline, ["38"], "EU")
    unknown = deepcopy(baseline)
    unknown.pop("size_system")
    cases["integer_without_system"] = result(unknown, ["38"], "EU")

    yml = '<shop><offers><offer id="synthetic-shoe" available="true"><name>Женские туфли</name><category>обувь</category><price>2000</price><currencyId>RUB</currencyId><param name="Размер" unit="UK">7,5</param></offer></offers></shop>'
    yml_row = parse_yml_catalog_xml(yml)[0]
    cases["scalar_yml_decimal_comma"] = result(yml_row, ["7.5", "7", "5"], "UK")

    csv = "id;name;category;available;price;currency;param\nsynthetic-shoe;Женские туфли;обувь;true;2000;RUB;Размер:7,5|Size Unit:UK\n"
    csv_row = parse_admitad_csv(csv)[0]
    cases["scalar_csv_decimal_comma"] = result(csv_row, ["7.5", "7", "5"], "UK")

    detailed = deepcopy(baseline)
    detailed["available_sizes_detailed"] = [{"size": "38", "size_system": "EU", "availability": "out_of_stock"}]
    cases["incoming_detail_stock_discarded"] = result(detailed, ["38"], "EU")

    direct = SimpleNamespace(category="обувь", category_name=None, source="synthetic",
        source_id=None, feed_raw_json={"sizes": "38", "size_system": "EU"}, raw_params_json={},
        available_sizes=["38"], available_sizes_detailed=[{"size": "38", "availability": "available"},
            {"size": "38", "availability": "in_stock"}])
    cases["synonymous_positive_detail_states"] = {
        "details_input": direct.available_sizes_detailed,
        "resolved": [s.to_dict() for s in resolve_product_sizes(direct)],
        "match_38_EU": exact_size_match(direct, "38", requested_system="EU"),
    }

    param_system = "id;name;category;available;price;currency;param\nsynthetic-shoe;Женские туфли;обувь;true;2000;RUB;Размер:38|Size System:EU\n"
    cases["csv_param_size_system_unrecognized_alias"] = result(parse_admitad_csv(param_system)[0], ["38"], "EU")

    evidence = json.loads((ROOT / "docs/modish2/foundation-size-evidence-20261006.json").read_text(encoding="utf-8-sig"))
    old_report = json.loads((ROOT / "docs/modish2/intent-v16-catalog-report.json").read_text(encoding="utf-8-sig"))
    shopping = next(s for s in evidence["sources"] if s["source"] == "shoppinglive")
    report = {
        "scope": "offline_synthetic_reproductions_and_checked_in_aggregate_reports",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "production_modified": False, "network_requests": False, "db_opened": False,
        "baseline_evidence": {
            "shoe_counts": old_report["category_size_evidence"]["обувь"],
            "shoe_samples": shopping["examples"]["обувь"],
            "aggregate_label_merges_system_and_stock_failure": True,
            "full_raw_field_inventory_available_locally": False,
            "note": "9351 is a system/stock aggregate, not evidence that 9351 stock signals are missing.",
        },
        "cases": cases,
        "limits": [
            "Synthetic defects are not evidence that those fields or decimal sizes occur in the Shopping Live snapshot.",
            "No RU/EU/LETTER conversion or merchant-wide size default was inferred.",
            "Incoming per-size details require a declared feed contract before supporting a schema/alias.",
            "Full source inventory must check preserved CSV param alongside expanded size/system fields.",
        ],
    }
    data = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        with args.output.open("x", encoding="utf-8") as out:
            out.write(data)
    print(data)


if __name__ == "__main__":
    main()
