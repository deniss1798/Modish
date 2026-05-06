"""
Синхронизация partner feed (этап 2 ТЗ).

  cd backend
  . .venv/Scripts/Activate.ps1
  python -m app.scripts.sync_partner_feed --source lamoda_ru
  python -m app.scripts.sync_partner_feed --source mock_lamoda --file test_feeds/mock_lamoda.yml
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def main() -> int:
  parser = argparse.ArgumentParser(description="Sync partner product feed")
  parser.add_argument("--source", required=True, help="ProductSource.code, e.g. lamoda_ru")
  parser.add_argument(
    "--file",
    help="Локальный файл фида (yml/xml/csv); без сети. Пример: test_feeds/mock_lamoda.yml",
  )
  args = parser.parse_args()

  from app.db import SessionLocal  # noqa: PLC0415
  from app.models import ProductSource  # noqa: PLC0415
  from app.services.catalog.feed_import_service import (  # noqa: PLC0415
    sync_partner_feed_by_code,
    sync_partner_feed_from_text,
  )
  from sqlalchemy import select  # noqa: PLC0415

  db = SessionLocal()
  run = None
  try:
    src = db.execute(select(ProductSource).where(ProductSource.code == args.source.strip())).scalar_one_or_none()
    if src is None:
      print(f"error: unknown ProductSource.code={args.source!r}", file=sys.stderr)
      return 1
    if args.file:
      path = Path(args.file)
      if not path.is_file():
        print(f"error: file not found: {path}", file=sys.stderr)
        return 1
      body = path.read_text(encoding="utf-8")
      run = sync_partner_feed_from_text(db, source=src, body=body, parser_url_hint=path.name)
    else:
      run = sync_partner_feed_by_code(db, args.source.strip())
    print(
      f"sync_run={run.id} status={run.status} total={run.total_received} "
      f"created={run.created_count} updated={run.updated_count} err={run.error_message!r}"
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
