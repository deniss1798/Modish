from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models import CatalogSyncRun, Product, ProductSource
from ..services.catalog.feed_import_service import sync_partner_feed

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
    "error_message": run.error_message,
  }


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
