from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...catalog_normalize import (
  infer_size_system,
  normalize_category,
  normalize_product_colors,
  normalize_sizes,
  product_gender_from_model,
)
from ...models import Product
from .catalog_quality import deactivate_ineligible_products


def renormalize_product_row(p: Product) -> bool:
  changed = False

  cat = normalize_category(p.category_name or p.category or "")
  if cat and cat != (p.category or ""):
    p.category = cat
    changed = True

  sizes = normalize_sizes(p.available_sizes or [])
  if sizes != (p.available_sizes or []):
    p.available_sizes = sizes
    changed = True

  sys = infer_size_system(sizes) or p.size_system
  if sys != p.size_system:
    p.size_system = sys
    changed = True

  colors = normalize_product_colors(p.colors or [])
  if colors != (p.colors or []):
    p.colors = colors
    changed = True

  gt = product_gender_from_model(p)
  if gt and gt != (p.gender_target or ""):
    p.gender_target = gt
    changed = True

  return changed


def renormalize_catalog(
  db: Session,
  *,
  source: str | None = None,
  limit: int | None = None,
) -> dict[str, int]:
  q = select(Product).where(Product.is_deleted_from_feed == 0)
  if source and source.strip():
    q = q.where(Product.source == source.strip())
  q = q.order_by(Product.updated_at.desc())
  if limit and limit > 0:
    q = q.limit(limit)
  rows = db.execute(q).scalars().all()
  updated = 0
  for p in rows:
    if renormalize_product_row(p):
      updated += 1
  hidden = deactivate_ineligible_products(db, source=source)
  db.commit()
  return {"processed": len(rows), "updated": updated, "deactivated": hidden}
