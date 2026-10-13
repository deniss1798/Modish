from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

import httpx
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ...integrations.admitad.feed_parser import (
  is_yml_catalog_xml,
  parse_admitad_csv,
  parse_admitad_xml,
  parse_admitad_yml,
  parse_yml_catalog_xml,
)
from ...models import CatalogRecord, CatalogSyncRun, Product, ProductSource
from ...log_redaction import redact
from ...config import get_admitad_export_config
from .catalog_records import store_record


@dataclass
class IngestResult:
  total_received: int = 0
  created: int = 0
  updated: int = 0
  deactivated: int = 0
  skipped: int = 0
  ingested: int = 0
  skip_breakdown: dict[str, int] = field(default_factory=dict)
  sync_ts: datetime | None = None

  def note_skip(self, reason: str) -> None:
    self.skipped += 1
    self.skip_breakdown[reason] = self.skip_breakdown.get(reason, 0) + 1


def _detect_parser(url: str, body: str):
  u = url.lower()
  if u.endswith(".csv") or "text/csv" in u or "format=csv" in u:
    return parse_admitad_csv(body)
  if u.endswith(".yml") or u.endswith(".yaml"):
    return parse_admitad_yml(body)
  if is_yml_catalog_xml(body):
    return parse_yml_catalog_xml(body)
  return parse_admitad_xml(body)


def _parse_feed_rows(url_hint: str, body: str) -> list:
  stripped = body.lstrip("\ufeff").strip()
  if not stripped:
    raise RuntimeError("Feed body is empty")
  rows = _detect_parser(url_hint, body)
  if not rows:
    looks_like_catalog = (
      is_yml_catalog_xml(stripped)
      or "<offer" in stripped.lower()
      or stripped.lower().startswith("id,")
    )
    if looks_like_catalog:
      raise RuntimeError("Feed parse produced 0 offers — check XML/YML format")
  return rows


def _download_feed_body(url: str, *, max_attempts: int = 4) -> str:
  """Скачивание фида с ретраями при сетевых сбоях и 429/5xx."""
  last_err: Exception | None = None
  timeout = httpx.Timeout(120.0, connect=30.0)
  for attempt in range(max_attempts):
    try:
      with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        r = client.get(url)
        if r.status_code in (429, 500, 502, 503, 504):
          wait = min(1.5 * (2**attempt), 45.0)
          ra = r.headers.get("Retry-After")
          if ra:
            try:
              wait = min(float(ra), 60.0)
            except ValueError:
              pass
          time.sleep(wait)
          last_err = RuntimeError(f"HTTP {r.status_code} from feed")
          continue
        r.raise_for_status()
        return r.text
    except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout, httpx.WriteTimeout) as exc:
      last_err = exc
      time.sleep(min(1.5 * (2**attempt), 25.0))
  msg = f"feed download failed after {max_attempts} attempts"
  if last_err:
    raise RuntimeError(f"{msg}: {redact(str(last_err))}") from None
  raise RuntimeError(msg)


def _ingest_feed_rows(
  db: Session,
  *,
  source: ProductSource,
  rows: list,
) -> IngestResult:
  """Парсинг строк фида с подсчётом created/updated/skipped."""
  result = IngestResult(
    total_received=len(rows),
    sync_ts=datetime.now(timezone.utc),
  )
  seen_external: set[str] = set()
  # Serialize imports/replays for one merchant; distinct merchants are independent.
  db.execute(select(ProductSource.id).where(ProductSource.id == source.id).with_for_update()).scalar_one()

  for row in rows:
    record, action = store_record(db, source, row, result.sync_ts)
    if record.external_id:
      seen_external.add(record.external_id)
    if action == "created":
      result.created += 1
    elif action == "updated":
      result.updated += 1
    else:
      result.skipped += 1
      for reason in record.reasons:
        result.skip_breakdown[reason] = result.skip_breakdown.get(reason, 0) + 1

  result.ingested = result.created + result.updated

  if seen_external:
    res = db.execute(
      update(Product)
      .where(
        Product.source_id == source.id,
        Product.external_id.notin_(list(seen_external)),
      )
      .values(
        is_deleted_from_feed=1,
        is_available=0,
        is_active=0,
        updated_at=result.sync_ts,
      )
    )
    result.deactivated = res.rowcount or 0
    db.execute(update(CatalogRecord).where(
      CatalogRecord.source_id == source.id,
      CatalogRecord.external_id.isnot(None),
      CatalogRecord.external_id.notin_(list(seen_external)),
    ).values(status="missing", reasons=["missing_from_feed"]))

  return result


def _finalize_run(
  db: Session,
  *,
  run: CatalogSyncRun,
  source: ProductSource,
  ingest: IngestResult,
  err: str | None,
  ok: bool,
) -> None:
  run.total_received = ingest.total_received
  run.created_count = ingest.created
  run.updated_count = ingest.updated
  run.deactivated_count = ingest.deactivated
  run.skipped_count = ingest.skipped
  run.ingested_count = ingest.ingested
  run.skip_breakdown = ingest.skip_breakdown
  run.error_message = err
  run.finished_at = datetime.now(timezone.utc)
  run.status = "ok" if ok else "error"
  source.status = "ok" if ok else "error"
  if ok and ingest.sync_ts is not None:
    source.last_sync_at = ingest.sync_ts


def sync_partner_feed(db: Session, *, source: ProductSource) -> CatalogSyncRun:
  now = datetime.now(timezone.utc)
  run = CatalogSyncRun(
    id=str(uuid4()),
    source_id=source.id,
    status="running",
    started_at=now,
  )
  db.add(run)
  db.flush()
  ingest = IngestResult()
  err: str | None = None
  try:
    if source.network == "admitad" and get_admitad_export_config() is None:
      raise ValueError("Admitad export is disabled")
    if not source.feed_url:
      raise RuntimeError("feed_url is empty")
    body = _download_feed_body(source.feed_url)
    rows = _parse_feed_rows(source.feed_url, body)
    with db.begin_nested():
      ingest = _ingest_feed_rows(db, source=source, rows=rows)
  except Exception as exc:  # noqa: BLE001
    err = redact(str(exc))[:2000]
  ok = err is None
  _finalize_run(db, run=run, source=source, ingest=ingest, err=err, ok=ok)
  db.commit()
  return run


def sync_partner_feed_from_text(
  db: Session,
  *,
  source: ProductSource,
  body: str,
  parser_url_hint: str = "feed.yml",
) -> CatalogSyncRun:
  """Импорт из уже загруженного текста (локальный mock-фид, тесты)."""
  now = datetime.now(timezone.utc)
  run = CatalogSyncRun(
    id=str(uuid4()),
    source_id=source.id,
    status="running",
    started_at=now,
  )
  db.add(run)
  db.flush()
  ingest = IngestResult()
  err: str | None = None
  try:
    rows = _parse_feed_rows(parser_url_hint, body)
    with db.begin_nested():
      ingest = _ingest_feed_rows(db, source=source, rows=rows)
  except Exception as exc:  # noqa: BLE001
    err = redact(str(exc))[:2000]
  ok = err is None
  _finalize_run(db, run=run, source=source, ingest=ingest, err=err, ok=ok)
  db.commit()
  return run


def sync_partner_feed_by_code(db: Session, code: str) -> CatalogSyncRun:
  src = db.execute(select(ProductSource).where(ProductSource.code == code)).scalar_one_or_none()
  if src is None:
    raise ValueError(f"Unknown source code: {code}")
  return sync_partner_feed(db, source=src)
