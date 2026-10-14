from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4
from urllib.parse import urljoin, urlsplit

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
from ..public_fetch import (
  BoundedBody, FetchDenied, MAX_REDIRECTS, REDIRECT_STATUSES, STREAM_CHUNK_BYTES,
  resolve_public_target_async,
)

_MAX_FEED_BYTES = 256 * 1024 * 1024
_FEED_DEADLINE_SECONDS = 120
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


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


async def _download_feed_once(url: str, deadline: float) -> str:
  target = url
  for hop in range(MAX_REDIRECTS + 1):
    public = await resolve_public_target_async(target)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
      raise FetchDenied('download_deadline')
    timeout = httpx.Timeout(min(120.0, remaining), connect=min(30.0, remaining))
    # Each origin gets a fresh TLS connection and no cookies/proxy from another
    # hop. The literal address cannot be rebound after admission.
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False) as client:
      async with client.stream('GET', public.connect_url, headers={
        'Host': public.host_header, 'Accept-Encoding': 'identity',
      }, extensions=public.extensions) as response:
        if response.status_code in REDIRECT_STATUSES:
          location = response.headers.get('location')
          if not location or hop == MAX_REDIRECTS:
            raise FetchDenied('invalid_download_redirect')
          target = urljoin(public.url, location)
          if urlsplit(public.url).scheme == 'https' and urlsplit(target).scheme != 'https':
            raise FetchDenied('download_redirect_downgrade')
          continue
        response.raise_for_status()
        body = BoundedBody(response.headers, _MAX_FEED_BYTES)
        async for chunk in response.aiter_raw(chunk_size=STREAM_CHUNK_BYTES):
          if time.monotonic() >= deadline:
            raise FetchDenied('download_deadline')
          body.add(chunk)
        raw = body.finish()
        # Keep the existing HTTP charset contract; UTF-8 is the default.
        return raw.decode(response.encoding or 'utf-8', errors='replace')
  raise FetchDenied('invalid_download_redirect')


async def _download_feed_with_retries(url: str, *, max_attempts: int) -> str:
  if not 1 <= max_attempts <= 4:
    raise ValueError('invalid_feed_attempt_count')
  deadline = time.monotonic() + _FEED_DEADLINE_SECONDS
  for attempt in range(max_attempts):
    wait = min(1.5 * (2 ** attempt), 25.0)
    try:
      return await _download_feed_once(url, deadline)
    except httpx.HTTPStatusError as exc:
      if exc.response.status_code not in _RETRY_STATUSES:
        raise
      retry_after = exc.response.headers.get('Retry-After')
      try:
        wait = max(0.0, min(float(retry_after), 60.0)) if retry_after else wait
      except ValueError:
        pass
    except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout, httpx.WriteTimeout):
      pass
    remaining = deadline - time.monotonic()
    if attempt + 1 == max_attempts or remaining <= 0:
      break
    await asyncio.sleep(min(wait, remaining))
  # Do not echo a provider URL: its query may contain partner credentials.
  raise RuntimeError('feed_download_unavailable') from None


async def _download_feed_body_async(url: str, *, max_attempts: int = 4) -> str:
  """One deadline includes DNS, headers, tiny chunks, redirects and retry waits."""
  try:
    async with asyncio.timeout(_FEED_DEADLINE_SECONDS):
      return await _download_feed_with_retries(url, max_attempts=max_attempts)
  except TimeoutError:
    raise FetchDenied('download_deadline') from None


def _download_feed_body(url: str, *, max_attempts: int = 4) -> str:
  """Compatible synchronous worker entry; never block the API's event loop."""
  try:
    asyncio.get_running_loop()
  except RuntimeError:
    pass
  else:
    raise RuntimeError('feed_download_requires_sync_worker')
  return asyncio.run(_download_feed_body_async(url, max_attempts=max_attempts))


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
