"""
Синхронизация partner feed (этап 2 ТЗ).

  cd backend
  . .venv/Scripts/Activate.ps1
  python -m app.scripts.sync_partner_feed --source lamoda_ru
"""
from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

load_dotenv()


def main() -> int:
  parser = argparse.ArgumentParser(description="Sync partner product feed")
  parser.add_argument("--source", required=True, help="ProductSource.code, e.g. lamoda_ru")
  args = parser.parse_args()

  from app.db import SessionLocal  # noqa: PLC0415
  from app.services.catalog.feed_import_service import sync_partner_feed_by_code  # noqa: PLC0415

  db = SessionLocal()
  run = None
  try:
    run = sync_partner_feed_by_code(db, args.source.strip())
    print(
      f"sync_run={run.id} status={run.status} total={run.total_received} "
      f"created={run.created_count} err={run.error_message!r}"
    )
  except Exception as exc:  # noqa: BLE001
    print(f"error: {exc}", file=sys.stderr)
    return 1
  finally:
    db.close()
  if run is None:
    return 1
  return 0 if run.status == "ok" else 2


if __name__ == "__main__":
  raise SystemExit(main())
