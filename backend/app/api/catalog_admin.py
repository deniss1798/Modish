from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models import CatalogSyncRun, Product, ProductSource, SourceRule
from ..services.catalog.feed_import_service import sync_partner_feed

RuleType = Literal["blocked_brand", "min_price", "blocked_category", "blocked_category_exact"]
RULE_TYPES: frozenset[str] = frozenset(
  ("blocked_brand", "min_price", "blocked_category", "blocked_category_exact")
)

router = APIRouter(prefix="/admin/catalog", tags=["admin-catalog"])


def get_db() -> Any:
  db = SessionLocal()
  try:
    yield db
  finally:
    db.close()


def _admin_auth(authorization: str | None) -> None:
  admin_token = (os.getenv("ADMIN_TOKEN") or "").strip()
  if not admin_token:
    raise HTTPException(status_code=503, detail="ADMIN_TOKEN is not configured")
  if authorization != f"Bearer {admin_token}":
    raise HTTPException(status_code=401, detail="Admin token required")


class ProductSourceCreate(BaseModel):
  code: str = Field(min_length=2, max_length=64)
  name: str = Field(min_length=1, max_length=128)
  network: str = Field(min_length=1, max_length=64)
  advertiser_id: str | None = None
  feed_url: str | None = None
  deeplink_template: str | None = None
  status: str = "pending"


class SourceRuleCreate(BaseModel):
  rule_type: RuleType
  rule_value: str = Field(min_length=1, max_length=4000)
  is_active: bool = True


class SourceRulePatch(BaseModel):
  rule_type: RuleType | None = None
  rule_value: str | None = Field(default=None, min_length=1, max_length=4000)
  is_active: bool | None = None


def _validate_rule_pair(rule_type: str, rule_value: str) -> None:
  rt = (rule_type or "").strip()
  rv = (rule_value or "").strip()
  if rt not in RULE_TYPES:
    raise HTTPException(status_code=400, detail=f"Unknown rule_type: {rt}")
  if not rv:
    raise HTTPException(status_code=400, detail="rule_value is required")
  if rt == "min_price":
    try:
      if int(rv) < 0:
        raise ValueError
    except ValueError as exc:
      raise HTTPException(status_code=400, detail="min_price rule_value must be a non-negative integer") from exc


def _rule_to_api(r: SourceRule) -> dict[str, Any]:
  return {
    "id": r.id,
    "source_id": r.source_id,
    "rule_type": r.rule_type,
    "rule_value": r.rule_value,
    "is_active": bool(r.is_active),
    "created_at": r.created_at.isoformat(),
    "updated_at": r.updated_at.isoformat(),
  }


@router.get("/sources")
def admin_catalog_sources(
  db: Session = Depends(get_db),
  authorization: str | None = Header(default=None),
) -> list[dict[str, Any]]:
  _admin_auth(authorization)
  rows = db.execute(select(ProductSource).order_by(ProductSource.code)).scalars().all()
  return [
    {
      "id": s.id,
      "code": s.code,
      "name": s.name,
      "network": s.network,
      "advertiser_id": s.advertiser_id,
      "feed_url": s.feed_url,
      "deeplink_template": s.deeplink_template,
      "status": s.status,
      "last_sync_at": s.last_sync_at.isoformat() if s.last_sync_at else None,
      "created_at": s.created_at.isoformat(),
      "updated_at": s.updated_at.isoformat(),
    }
    for s in rows
  ]


@router.post("/sources")
def admin_catalog_sources_create(
  payload: ProductSourceCreate,
  db: Session = Depends(get_db),
  authorization: str | None = Header(default=None),
) -> dict[str, Any]:
  _admin_auth(authorization)
  exists = db.execute(select(ProductSource).where(ProductSource.code == payload.code)).scalar_one_or_none()
  if exists:
    raise HTTPException(status_code=409, detail="Source code already exists")
  now = datetime.now(timezone.utc)
  s = ProductSource(
    id=str(uuid4()),
    code=payload.code.strip(),
    name=payload.name.strip(),
    network=payload.network.strip(),
    advertiser_id=(payload.advertiser_id or "").strip() or None,
    feed_url=(payload.feed_url or "").strip() or None,
    deeplink_template=(payload.deeplink_template or "").strip() or None,
    status=payload.status.strip() or "pending",
    created_at=now,
    updated_at=now,
  )
  db.add(s)
  db.commit()
  return {"id": s.id, "code": s.code}


@router.post("/sources/{source_id}/sync")
def admin_catalog_source_sync(
  source_id: str,
  db: Session = Depends(get_db),
  authorization: str | None = Header(default=None),
) -> dict[str, Any]:
  _admin_auth(authorization)
  src = db.execute(select(ProductSource).where(ProductSource.id == source_id)).scalar_one_or_none()
  if src is None:
    raise HTTPException(status_code=404, detail="Source not found")
  run = sync_partner_feed(db, source=src)
  return {
    "sync_run_id": run.id,
    "status": run.status,
    "total_received": run.total_received,
    "created_count": run.created_count,
    "updated_count": run.updated_count,
    "deactivated_count": run.deactivated_count,
    "error_message": run.error_message,
    "started_at": run.started_at.isoformat(),
    "finished_at": run.finished_at.isoformat() if run.finished_at else None,
  }


@router.get("/sources/{source_id}/rules")
def admin_catalog_source_rules(
  source_id: str,
  db: Session = Depends(get_db),
  authorization: str | None = Header(default=None),
  include_inactive: bool = False,
) -> list[dict[str, Any]]:
  _admin_auth(authorization)
  src = db.execute(select(ProductSource).where(ProductSource.id == source_id)).scalar_one_or_none()
  if src is None:
    raise HTTPException(status_code=404, detail="Source not found")
  q = select(SourceRule).where(SourceRule.source_id == source_id)
  if not include_inactive:
    q = q.where(SourceRule.is_active == 1)
  q = q.order_by(SourceRule.created_at)
  rows = db.execute(q).scalars().all()
  return [_rule_to_api(r) for r in rows]


@router.post("/sources/{source_id}/rules")
def admin_catalog_source_rule_create(
  source_id: str,
  payload: SourceRuleCreate,
  db: Session = Depends(get_db),
  authorization: str | None = Header(default=None),
) -> dict[str, Any]:
  _admin_auth(authorization)
  src = db.execute(select(ProductSource).where(ProductSource.id == source_id)).scalar_one_or_none()
  if src is None:
    raise HTTPException(status_code=404, detail="Source not found")
  _validate_rule_pair(payload.rule_type, payload.rule_value)
  now = datetime.now(timezone.utc)
  r = SourceRule(
    id=str(uuid4()),
    source_id=source_id,
    rule_type=payload.rule_type.strip(),
    rule_value=payload.rule_value.strip(),
    is_active=1 if payload.is_active else 0,
    created_at=now,
    updated_at=now,
  )
  db.add(r)
  db.commit()
  return _rule_to_api(r)


@router.patch("/rules/{rule_id}")
def admin_catalog_rule_patch(
  rule_id: str,
  payload: SourceRulePatch,
  db: Session = Depends(get_db),
  authorization: str | None = Header(default=None),
) -> dict[str, Any]:
  _admin_auth(authorization)
  r = db.execute(select(SourceRule).where(SourceRule.id == rule_id)).scalar_one_or_none()
  if r is None:
    raise HTTPException(status_code=404, detail="Rule not found")
  rt = payload.rule_type if payload.rule_type is not None else r.rule_type
  rv = payload.rule_value if payload.rule_value is not None else r.rule_value
  _validate_rule_pair(rt, rv)
  if payload.rule_type is not None:
    r.rule_type = payload.rule_type.strip()
  if payload.rule_value is not None:
    r.rule_value = payload.rule_value.strip()
  if payload.is_active is not None:
    r.is_active = 1 if payload.is_active else 0
  r.updated_at = datetime.now(timezone.utc)
  db.commit()
  return _rule_to_api(r)


@router.delete("/rules/{rule_id}")
def admin_catalog_rule_delete(
  rule_id: str,
  db: Session = Depends(get_db),
  authorization: str | None = Header(default=None),
) -> dict[str, Any]:
  _admin_auth(authorization)
  r = db.execute(select(SourceRule).where(SourceRule.id == rule_id)).scalar_one_or_none()
  if r is None:
    raise HTTPException(status_code=404, detail="Rule not found")
  db.delete(r)
  db.commit()
  return {"deleted": True, "id": rule_id}


@router.get("/sync-runs")
def admin_catalog_sync_runs(
  limit: int = 50,
  db: Session = Depends(get_db),
  authorization: str | None = Header(default=None),
) -> list[dict[str, Any]]:
  _admin_auth(authorization)
  rows = (
    db.execute(
      select(CatalogSyncRun).order_by(CatalogSyncRun.started_at.desc()).limit(min(200, max(1, limit)))
    )
    .scalars()
    .all()
  )
  return [
    {
      "id": r.id,
      "source_id": r.source_id,
      "status": r.status,
      "total_received": r.total_received,
      "created_count": r.created_count,
      "updated_count": r.updated_count,
      "deactivated_count": r.deactivated_count,
      "error_message": r.error_message,
      "started_at": r.started_at.isoformat(),
      "finished_at": r.finished_at.isoformat() if r.finished_at else None,
    }
    for r in rows
  ]


@router.get("/products/stats")
def admin_catalog_product_stats(
  db: Session = Depends(get_db),
  authorization: str | None = Header(default=None),
) -> dict[str, Any]:
  _admin_auth(authorization)
  total = db.execute(select(func.count()).select_from(Product)).scalar_one()
  active = db.execute(select(func.count()).select_from(Product).where(Product.is_active == 1)).scalar_one()
  with_affiliate = db.execute(
    select(func.count()).select_from(Product).where(Product.affiliate_url.isnot(None), Product.affiliate_url != "")
  ).scalar_one()
  return {
    "total_products": int(total or 0),
    "active_products": int(active or 0),
    "with_affiliate_url": int(with_affiliate or 0),
  }
