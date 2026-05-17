from __future__ import annotations

from ...catalog_normalize import normalize_category
from ...models import Product


def product_has_purchasable_link(product: Product) -> bool:
  aff = (product.affiliate_url or "").strip()
  url = (product.product_url or "").strip()
  return bool(aff or url)


def product_is_feed_eligible(product: Product) -> bool:
  """Скрываем товары без фото, цены, ссылки или категории."""
  if not product.is_active or not product.is_available:
    return False
  if product.is_deleted_from_feed:
    return False
  if int(product.price or 0) <= 0:
    return False
  img = (product.image_url or "").strip()
  if not img:
    return False
  if not product_has_purchasable_link(product):
    return False
  cat = normalize_category(product.category or product.category_name or "")
  if not cat or cat in ("разное", "other", "misc"):
    merchant = (product.merchant_category or product.category_name or "").strip()
    if not merchant and not (product.category_name or "").strip():
      return False
  return True


def deactivate_ineligible_products(db, *, source: str | None = None) -> int:
  from sqlalchemy import select

  q = select(Product).where(Product.is_active == 1)
  if source and source.strip():
    q = q.where(Product.source == source.strip())
  rows = db.execute(q).scalars().all()
  n = 0
  for p in rows:
    if product_is_feed_eligible(p):
      continue
    p.is_active = 0
    n += 1
  return n
