from __future__ import annotations

from ...catalog_normalize import normalize_category
from ...models import Product
from ..catalog_audience import product_is_adult
from ..catalog_scope import product_is_fashion
from .affiliate_link_service import _public_web_url
from .stock_freshness import stock_observation_problem


def product_has_purchasable_link(product: Product) -> bool:
  from .affiliate_link_service import resolve_outbound_url
  return bool(resolve_outbound_url(product))


def product_is_feed_eligible(product: Product, *, checked_at=None) -> bool:
  """Скрываем товары без фото, цены, ссылки или категории."""
  if not product_is_adult(product) or not product_is_fashion(product):
    return False
  if not product.is_active or not product.is_available:
    return False
  if product.is_deleted_from_feed:
    return False
  if stock_observation_problem(product, checked_at=checked_at):
    return False
  if int(product.price or 0) <= 0:
    return False
  img = (product.image_url or "").strip()
  if not _public_web_url(img):
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
