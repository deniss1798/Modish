"""Offline Catalog Foundation report. No .env, network, or production DB reads."""
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import argparse
import os
import sys
import json
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("feed", type=Path)
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "backend"))
    environment = {"DATABASE_URL": "sqlite:///:memory:", "APP_ENV": "test",
        "JWT_SECRET": "offline-audit-not-for-deployment", "PYTHON_DOTENV_DISABLED": "1"}
    # Analysis needs normalized objects, not a database connection.
    with patch.dict(os.environ, environment, clear=True), patch("dotenv.load_dotenv"):
        from app.models import ProductSource
        from app.services.catalog.feed_import_service import _parse_feed_rows
        from app.services.catalog.catalog_records import prepare_record
        from app.services.catalog.versions import NORMALIZATION_VERSION
        rows = _parse_feed_rows(args.feed.name, args.feed.read_text(encoding="utf-8-sig"))
        source = ProductSource(id="offline", code=args.source, name=args.source, network="direct")
        counts, reasons, examples = Counter(), Counter(), {}
        now = datetime.now(timezone.utc)
        for row in rows:
            _, _, _, canonical, decision = prepare_record(row, source, now)
            counts[decision.status] += 1
            reasons.update(decision.reasons)
            for reason in decision.reasons:
                if len(examples.setdefault(reason, [])) < 3:
                    examples[reason].append({"external_id": row.get("id"),
                        "title": (canonical or {}).get("title"),
                        "sizes": (canonical or {}).get("available_sizes")})
        report = {"generated_at": now.isoformat(), "normalization_version": NORMALIZATION_VERSION,
            "source": args.source, "input_file": args.feed.name, "rows": len(rows),
            "counts": dict(counts), "reasons": dict(reasons), "examples": examples,
            "pass_rate": counts["passed"] / len(rows) if rows else None,
            "scope": "local_feed_snapshot_only", "network_urls_verified": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "examples"}, ensure_ascii=True))


if __name__ == "__main__":
    main()
