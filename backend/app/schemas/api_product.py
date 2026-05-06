"""Сериализация Product для REST API."""
from __future__ import annotations

from typing import Any

from ..catalog_normalize import normalize_category
from ..models import Product

# Человекочитаемые названия витрин (source code → подпись в приложении).
_SHOP_LABEL_BY_SOURCE: dict[str, str] = {
  "demo": "Демо",
  "lamoda": "Lamoda",
  "lamoda_ru": "Lamoda",
  "wildberries": "Wildberries",
  "wb": "Wildberries",
  "admitad": "Партнёрский каталог",
}


def shop_label_for_product(p: Product) -> str:
  code = (p.source or "").strip().lower()
  if code in _SHOP_LABEL_BY_SOURCE:
    return _SHOP_LABEL_BY_SOURCE[code]
  brand = (p.brand or "").strip()
  if brand:
    return brand
  if code:
    return code.replace("_", " ").strip().title() or "Магазин"
  return "Магазин"


def product_to_api(p: Product) -> dict[str, Any]:
  return {
    "id": p.id,
    "external_id": p.external_id,
    "source": p.source,
    "source_id": p.source_id,
    "title": p.title,
    "brand": p.brand,
    "category": p.category,
    "subcategory": p.subcategory,
    "price": p.price,
    "old_price": p.old_price,
    "discount_percent": p.discount_percent,
    "currency": p.currency,
    "availability_status": p.availability_status,
    "image_url": p.image_url,
    "product_url": p.product_url,
    "affiliate_url": p.affiliate_url,
    "original_url": p.original_url,
    "merchant_category": p.merchant_category,
    "merchant_subcategory": p.merchant_subcategory,
    "available_sizes": p.available_sizes or [],
    "available_sizes_detailed": p.available_sizes_detailed or [],
    "size_system": p.size_system,
    "colors": p.colors or [],
    "color_family": p.color_family,
    "material": p.material,
    "season": p.season,
    "occasion": p.occasion,
    "gender_target": p.gender_target,
    "fit": p.fit,
    "silhouette": p.silhouette,
    "style_tags": p.style_tags or [],
    "image_quality_score": p.image_quality_score,
    "is_available": bool(p.is_available),
    "last_checked_at": p.last_checked_at.isoformat() if p.last_checked_at else None,
    "is_active": bool(p.is_active),
    "last_seen_in_feed_at": p.last_seen_in_feed_at.isoformat() if p.last_seen_in_feed_at else None,
    "is_deleted_from_feed": bool(p.is_deleted_from_feed),
    "shop_label": shop_label_for_product(p),
  }
