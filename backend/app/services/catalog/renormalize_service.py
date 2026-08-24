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
from .style_tagger import infer_style_tags


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

  # цвета: сначала из текущего поля, при пустом — из исходного цвета фида
  color_sources = list(p.colors or [])
  if p.color_original:
    color_sources.append(p.color_original)
  colors = normalize_product_colors(color_sources)
  if colors != (p.colors or []):
    p.colors = colors
    changed = True

  gt = product_gender_from_model(p)
  if gt and gt != (p.gender_target or ""):
    p.gender_target = gt
    changed = True

  # style-теги: раньше при импорте всегда были пустыми — заполняем правилами
  tags = infer_style_tags(
    title=p.title,
    description=p.description,
    category=p.category or cat,
    subcategory=p.subcategory,
  )
  if tags != (p.style_tags or []):
    p.style_tags = tags
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
