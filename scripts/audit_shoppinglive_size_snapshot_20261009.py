"""Bounded read-only inventory of an existing Shopping Live CSV snapshot.

No downloads, URLs, credentials, database access or catalogue writes. The output
contains aggregates only and never replaces an existing report. The default
SHA256 pins the snapshot in foundation-download-report.json (6 October).
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.integrations.admitad.feed_parser import parse_admitad_csv
from app.services.catalog.merchant_adapters import get_adapter
from app.services.catalog.product_normalizer import normalized_to_dict
from app.services.catalog.size_resolver import explicit_size_system, resolve_product_sizes

PINNED_SHA256 = "58a241109f4d1f368c42be5e413399bc995e2dd31d73a26cc5917db035ff90b0"
SYSTEM_TOKEN = re.compile(r"RU|RUS|EU|EURO|UK|US|USA|INT|INTERNATIONAL|LETTER|LETTERS|WAIST", re.I)
SAFE_SIZE = re.compile(r"(?:RU|EU|US|UK|INT|LETTER)?[ :\-]?(?:\d{1,2}(?:[,.]5)?|X{0,3}[SML]|[2-6]XL|W\d{2}(?:L\d{2})?)", re.I)


def safe_system(value):
    token = str(value or "").strip()
    return token.upper() if SYSTEM_TOKEN.fullmatch(token) else "empty" if not token else "unrecognized_value"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-sha256", default=PINNED_SHA256)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Output already exists; choose a new report file")
    if not args.snapshot.is_file() or args.snapshot.stat().st_size > 60 * 1024 * 1024:
        raise SystemExit("Expected an existing local CSV smaller than 60 MiB")
    data = args.snapshot.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != args.expected_sha256:
        raise SystemExit("Snapshot SHA256 differs from the supplied expected SHA256")
    rows = parse_admitad_csv(data.decode("utf-8-sig"))
    if len(rows) > 50000:
        raise SystemExit("Snapshot exceeds bounded 50000-row audit")
    adapter = get_adapter("shoppinglive")
    source = SimpleNamespace(code="shoppinglive")
    counts = Counter()
    reason_counts = Counter()
    availability_counts = Counter()
    explicit_system_counts = Counter()
    shoe_field_counts = Counter()
    param_name_counts = Counter()
    param_size_values = Counter()
    system_candidate_values = {}
    decimal_raw_size_count = 0
    detailed_stock_present_count = 0
    for row in rows:
        n = adapter.normalize(row, source)
        if n is None or n.category != "обувь":
            continue
        counts["all_shoe_rows"] += 1
        payload = normalized_to_dict(n)
        resolved = resolve_product_sizes(SimpleNamespace(**payload))
        counts["in_stock_shoe_rows"] += n.availability_status == "in_stock"
        availability_counts[n.availability_status or "unknown"] += 1
        explicit_system_counts[explicit_size_system(n.raw_data, n.raw_params) or "unknown"] += 1
        reason_counts.update(size.reason or "known" for size in resolved)
        counts["has_known_available_size"] += any(size.known and size.availability == "available" for size in resolved)
        counts["has_available_size_unknown_system"] += any(size.availability == "available" and size.reason == "size_system_unknown" for size in resolved)
        counts["has_unavailable_size"] += any(size.availability == "unavailable" for size in resolved)
        counts["has_unknown_availability_size"] += any(size.availability == "unknown" for size in resolved)
        if row.get("available_sizes_detailed"):
            detailed_stock_present_count += 1
        for key, value in row.items():
            lower = str(key).lower()
            if "size" in lower or "размер" in lower:
                shoe_field_counts[lower] += 1
                if "unit" in lower or "system" in lower or "систем" in lower:
                    values = value if isinstance(value, list) else [value]
                    system_candidate_values.setdefault(lower, Counter()).update(safe_system(v) for v in values)
        original_param = row.get("param") or row.get("Param") or ""
        if isinstance(original_param, str):
            for part in original_param.split("|"):
                if ":" not in part:
                    continue
                name, value = (p.strip() for p in part.split(":", 1))
                lower = name.lower()
                if "size" not in lower and "размер" not in lower:
                    continue
                param_name_counts[lower] += 1
                if lower in {"размер", "size"}:
                    if SAFE_SIZE.fullmatch(value):
                        param_size_values[value] += 1
                    else:
                        param_size_values["other_or_compound"] += 1
                    if re.fullmatch(r"\d{1,2},5", value):
                        decimal_raw_size_count += 1
                elif "unit" in lower or "system" in lower or "систем" in lower:
                    system_candidate_values.setdefault("original_param:" + lower, Counter()).update([safe_system(value)])
    report = {
        "scope": "read_only_existing_csv_snapshot_aggregate_inventory",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "source_code": "shoppinglive", "sha256": digest, "bytes": len(data), "row_count": len(rows),
        "production_modified": False, "network_requests": False, "db_opened": False,
        "counts": dict(counts), "stock_by_shoe_row": dict(availability_counts),
        "explicit_system_by_shoe_row": dict(explicit_system_counts),
        "reason_by_size_token": dict(reason_counts), "shoe_size_field_presence": dict(shoe_field_counts),
        "original_size_param_names": dict(param_name_counts),
        "original_safe_size_values": dict(param_size_values),
        "candidate_system_values": {k: dict(v) for k, v in system_candidate_values.items()},
        "decimal_comma_scalar_in_original_param_rows": decimal_raw_size_count,
        "incoming_available_sizes_detailed_rows": detailed_stock_present_count,
        "limits": [
            "A CSV snapshot is a dated observation; this audit does not assert current stock freshness.",
            "All shoe rows include unavailable/quarantined entries; they are not necessarily the 9351 active DB SKUs.",
            "No size conversion or merchant-wide RU/EU default is enabled.",
        ],
    }
    with args.output.open("x", encoding="utf-8") as out:
        json.dump(report, out, ensure_ascii=False, indent=2)
        out.write("\n")
    print(json.dumps({"result": "completed", "row_count": len(rows), "counts": dict(counts)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
