from __future__ import annotations

import time
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
from ...models import CatalogSyncRun, Product, ProductSource
from .affiliate_link_service import apply_deeplink_for_product
from .feed_row_mapper import row_to_normalized
from .product_normalizer import orm_kwargs_from_normalized


def _detect_parser(url: str, body: str):
  u = url.lower()
  if u.endswith(".csv") or "text/csv" in u:
    return parse_admitad_csv(body)
  if u.endswith(".yml") or u.endswith(".yaml"):
    return parse_admitad_yml(body)
  if is_yml_catalog_xml(body):
    return parse_yml_catalog_xml(body)
  return parse_admitad_xml(body)


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
    raise RuntimeError(f"{msg}: {last_err}") from last_err
  raise RuntimeError(msg)


def _ingest_feed_rows(
  db: Session,
  *,
  source: ProductSource,
  rows: list,
) -> tuple[int, int, int, int, datetime]:
  """Парсинг строк фида: (total_received, created, updated, deactivated, sync_ts)."""
  sync_ts = datetime.now(timezone.utc)
  created = updated = 0
  seen_external: set[str] = set()
  for row in rows:
    n = row_to_normalized(row, source)
    if n is None:
      continue
    img = (n.image_url or "").strip()
    raw_dest = (n.affiliate_url or n.original_url or "").strip()
    if not img or not raw_dest:
      continue

    payload = orm_kwargs_from_normalized(n, source_id=source.id, sync_ts=sync_ts)
    raw_aff = (n.affiliate_url or "").strip()
    raw_orig = (n.original_url or "").strip()
    base_for_tpl = raw_aff or raw_orig
    aff_templated = apply_deeplink_for_product(
      base_for_tpl,
      source,
      external_id=n.external_id,
      original_url=raw_orig or raw_aff,
    )
    payload["affiliate_url"] = aff_templated if aff_templated else None
    payload["original_url"] = raw_orig or None
    payload["product_url"] = (aff_templated or raw_orig or raw_aff).strip()

    seen_external.add(n.external_id)
    existing = db.execute(
      select(Product).where(
        Product.external_id == n.external_id,
        Product.source == source.code,
      )
    ).scalar_one_or_none()

    if existing is None:
      pid = str(uuid4())
      p = Product(id=pid, **payload)
      db.add(p)
      created += 1
    else:
      for k, v in payload.items():
        setattr(existing, k, v)
      existing.updated_at = sync_ts
      updated += 1

  deactivated = 0
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
        updated_at=sync_ts,
      )
    )
    deactivated = res.rowcount or 0

  return len(rows), created, updated, deactivated, sync_ts


def _finalize_run(
  db: Session,
  *,
  run: CatalogSyncRun,
  source: ProductSource,
  total: int,
  created: int,
  updated: int,
  deactivated: int,
  err: str | None,
  sync_ts: datetime | None,
  ok: bool,
) -> None:
  run.total_received = total
  run.created_count = created
  run.updated_count = updated
  run.deactivated_count = deactivated
  run.error_message = err
  run.finished_at = datetime.now(timezone.utc)
  run.status = "ok" if ok else "error"
  source.status = "ok" if ok else "error"
  if ok and sync_ts is not None:
    source.last_sync_at = sync_ts


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
  total = created = updated = deactivated = 0
  err: str | None = None
  sync_ts: datetime | None = None
  try:
    if not source.feed_url:
      raise RuntimeError("feed_url is empty")
    body = _download_feed_body(source.feed_url)
    total, created, updated, deactivated, sync_ts = _ingest_feed_rows(
      db,
      source=source,
      rows=_detect_parser(source.feed_url, body),
    )
  except Exception as exc:  # noqa: BLE001
    err = str(exc)[:2000]
  ok = err is None
  _finalize_run(
    db,
    run=run,
    source=source,
    total=total,
    created=created,
    updated=updated,
    deactivated=deactivated,
    err=err,
    sync_ts=sync_ts,
    ok=ok,
  )
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
  total = created = updated = deactivated = 0
  err: str | None = None
  sync_ts: datetime | None = None
  try:
    rows = _detect_parser(parser_url_hint, body)
    total, created, updated, deactivated, sync_ts = _ingest_feed_rows(db, source=source, rows=rows)
  except Exception as exc:  # noqa: BLE001
    err = str(exc)[:2000]
  ok = err is None
  _finalize_run(
    db,
    run=run,
    source=source,
    total=total,
    created=created,
    updated=updated,
    deactivated=deactivated,
    err=err,
    sync_ts=sync_ts,
    ok=ok,
  )
  db.commit()
  return run


def sync_partner_feed_by_code(db: Session, code: str) -> CatalogSyncRun:
  src = db.execute(select(ProductSource).where(ProductSource.code == code)).scalar_one_or_none()
  if src is None:
    raise ValueError(f"Unknown source code: {code}")
  return sync_partner_feed(db, source=src)
