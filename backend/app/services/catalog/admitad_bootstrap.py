"""Создание Admitad CSV-источников и правил «только одежда»."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...integrations.admitad.clothing_rules import DEFAULT_CLOTHING_ONLY_RULES
from ...integrations.admitad.source_presets import ADMITAD_CSV_SOURCES, AdmitadCsvSourcePreset
from ...models import ProductSource, SourceRule
from .feed_import_service import sync_partner_feed


def _ensure_clothing_rules(db: Session, *, source_id: str) -> int:
  """Добавляет недостающие правила; возвращает число новых."""
  existing = db.execute(
    select(SourceRule).where(
      SourceRule.source_id == source_id,
      SourceRule.is_active == 1,
    )
  ).scalars().all()
  have = {(r.rule_type, (r.rule_value or "").strip()) for r in existing}
  now = datetime.now(timezone.utc)
  added = 0
  for rule_type, rule_value in DEFAULT_CLOTHING_ONLY_RULES:
    key = (rule_type, rule_value)
    if key in have:
      continue
    db.add(
      SourceRule(
        id=str(uuid4()),
        source_id=source_id,
        rule_type=rule_type,
        rule_value=rule_value,
        is_active=1,
        created_at=now,
        updated_at=now,
      )
    )
    added += 1
  return added


def _upsert_source(db: Session, preset: AdmitadCsvSourcePreset) -> tuple[ProductSource, bool]:
  existing = db.execute(
    select(ProductSource).where(ProductSource.code == preset.code)
  ).scalar_one_or_none()
  now = datetime.now(timezone.utc)
  if existing is not None:
    existing.name = preset.name
    existing.network = preset.network
    existing.feed_url = preset.feed_url
    if preset.advertiser_id:
      existing.advertiser_id = preset.advertiser_id
    existing.status = "ok"
    existing.updated_at = now
    return existing, False
  s = ProductSource(
    id=str(uuid4()),
    code=preset.code,
    name=preset.name,
    network=preset.network,
    advertiser_id=preset.advertiser_id,
    feed_url=preset.feed_url,
    deeplink_template=None,
    status="ok",
    created_at=now,
    updated_at=now,
  )
  db.add(s)
  db.flush()
  return s, True


def bootstrap_admitad_csv_sources(
  db: Session,
  *,
  codes: list[str] | None = None,
  sync: bool = False,
) -> dict[str, Any]:
  """
  Регистрирует FABLE / Aim Clo (Admitad CSV) и правила «только одежда».
  codes: подмножество ['fable', 'aimclo']; None — все пресеты.
  """
  presets = list(ADMITAD_CSV_SOURCES)
  if codes:
    want = {c.strip().lower() for c in codes if c.strip()}
    presets = [p for p in presets if p.code in want]
    if not presets:
      raise ValueError(f"No presets for codes: {sorted(want)}")

  results: list[dict[str, Any]] = []
  for preset in presets:
    src, created = _upsert_source(db, preset)
    rules_added = _ensure_clothing_rules(db, source_id=src.id)
    db.commit()

    sync_info: dict[str, Any] | None = None
    if sync:
      run = sync_partner_feed(db, source=src)
      sync_info = {
        "run_id": run.id,
        "status": run.status,
        "ingested": run.ingested_count,
        "created": run.created_count,
        "updated": run.updated_count,
        "skipped": run.skipped_count,
        "error": run.error_message,
      }

    results.append(
      {
        "code": src.code,
        "source_id": src.id,
        "created": created,
        "feed_url": src.feed_url,
        "rules_added": rules_added,
        "sync": sync_info,
      }
    )

  return {"ok": True, "sources": results}
