"""Админ: импорт товаров (bulk JSON)."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..catalog_normalize import normalize_category, normalize_product_colors
from ..db import SessionLocal
from ..models import Product

router = APIRouter(prefix="/admin/products", tags=["admin-products"])


def get_db() -> Any:
  db = SessionLocal()
  try:
    yield db
  finally:
    db.close()


class ProductIn(BaseModel):
  external_id: str
  source: str
  title: str
  brand: str
  category: str
  subcategory: str | None = None
  price: int = Field(ge=0)
  currency: str = "RUB"
  image_url: str
  product_url: str
  available_sizes: list[str] = Field(default_factory=list)
  available_sizes_detailed: list[dict[str, Any]] = Field(default_factory=list)
  size_system: str | None = None
  colors: list[str] = Field(default_factory=list)
  color_family: str | None = None
  material: str | None = None
  season: str | None = None
  occasion: str | None = None
  gender_target: str | None = None
  fit: str | None = None
  silhouette: str | None = None
  style_tags: list[str] = Field(default_factory=list)
  image_quality_score: float | None = None
  is_available: bool = True
  is_active: bool = True


def _admin_auth(authorization: str | None) -> None:
  admin_token = (os.getenv("ADMIN_TOKEN") or "").strip()
  if not admin_token:
    raise HTTPException(status_code=503, detail="ADMIN_TOKEN is not configured")
  if authorization != f"Bearer {admin_token}":
    raise HTTPException(status_code=401, detail="Admin token required")


@router.post("/import")
def admin_products_import(
  items: list[ProductIn],
  db: Session = Depends(get_db),
  authorization: str | None = Header(default=None),
) -> dict[str, Any]:
  _admin_auth(authorization)
  now = datetime.now(timezone.utc)
  created = 0
  updated = 0
  for it in items:
    existing = db.execute(
      select(Product).where(Product.external_id == it.external_id, Product.source == it.source)
    ).scalar_one_or_none()
    payload = it.model_dump()
    if existing is None:
      p = Product(
        id=str(uuid4()),
        external_id=payload["external_id"],
        source=payload["source"],
        title=payload["title"],
        brand=payload["brand"],
        category=normalize_category(payload["category"]),
        subcategory=payload.get("subcategory"),
        price=int(payload["price"]),
        currency=payload.get("currency") or "RUB",
        image_url=payload["image_url"],
        product_url=payload["product_url"],
        available_sizes=payload.get("available_sizes") or [],
        available_sizes_detailed=payload.get("available_sizes_detailed") or [],
        size_system=payload.get("size_system"),
        colors=normalize_product_colors(payload.get("colors") or []),
        color_family=payload.get("color_family"),
        material=payload.get("material"),
        season=payload.get("season"),
        occasion=payload.get("occasion"),
        gender_target=payload.get("gender_target"),
        fit=payload.get("fit"),
        silhouette=payload.get("silhouette"),
        style_tags=payload.get("style_tags") or [],
        image_quality_score=payload.get("image_quality_score"),
        is_available=1 if payload.get("is_available", True) else 0,
        last_checked_at=now,
        is_active=1 if payload.get("is_active", True) else 0,
        created_at=now,
        updated_at=now,
      )
      db.add(p)
      created += 1
    else:
      existing.title = payload["title"]
      existing.brand = payload["brand"]
      existing.category = normalize_category(payload["category"])
      existing.subcategory = payload.get("subcategory")
      existing.price = int(payload["price"])
      existing.currency = payload.get("currency") or "RUB"
      existing.image_url = payload["image_url"]
      existing.product_url = payload["product_url"]
      existing.available_sizes = payload.get("available_sizes") or []
      existing.available_sizes_detailed = payload.get("available_sizes_detailed") or []
      existing.size_system = payload.get("size_system")
      existing.colors = normalize_product_colors(payload.get("colors") or [])
      existing.color_family = payload.get("color_family")
      existing.material = payload.get("material")
      existing.season = payload.get("season")
      existing.occasion = payload.get("occasion")
      existing.gender_target = payload.get("gender_target")
      existing.fit = payload.get("fit")
      existing.silhouette = payload.get("silhouette")
      existing.style_tags = payload.get("style_tags") or []
      existing.image_quality_score = payload.get("image_quality_score")
      existing.is_available = 1 if payload.get("is_available", True) else 0
      existing.last_checked_at = now
      existing.is_active = 1 if payload.get("is_active", True) else 0
      existing.updated_at = now
      updated += 1
  db.commit()
  return {"created": created, "updated": updated}
