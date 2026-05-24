"""Сериализация Product для REST API."""
from __future__ import annotations

from typing import Any

from ..models import Product

# Человекочитаемые названия витрин (source code → подпись в приложении).
_SHOP_LABEL_BY_SOURCE: dict[str, str] = {
  "demo": "Демо",
  "lamoda": "Lamoda",
  "lamoda_ru": "Lamoda",
  "wildberries": "Wildberries",
  "wb": "Wildberries",
  "admitad": "Партнёрский каталог",
  "befree": "Befree",
  "fable": "FABLE",
  "aimclo": "Aim Clo",
  "sportmaster": "Спортмастер",
  "shoppinglive": "Shopping Live",
  "postmeridiem": "Post Meridiem",
  "baon": "BAON",
  "mongolshop": "MONGOLSHOP",
  "serginnetti": "SERGINNETTI",
}


def _api_discount_percent(p: Product) -> int | None:
  """Процент скидки для API: из БД или из old_price/price."""
  if p.discount_percent is not None:
    return int(p.discount_percent)
  op = p.old_price
  pr = p.price
  if op is not None and pr is not None and op > pr > 0:
    return int(round(100.0 * (op - pr) / float(op)))
  return None


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
    "offer_id": p.external_id,
    "source": p.source,
    "source_id": p.source_id,
    "title": p.title,
    "brand": p.brand,
    "category": p.category,
    "subcategory": p.subcategory,
    "price": p.price,
    "old_price": p.old_price,
    "discount_percent": _api_discount_percent(p),
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
    "group_id": p.group_id,
    "description": p.description,
    "image_urls": p.image_urls or [],
    "barcode": p.barcode,
    "vendor_code": p.vendor_code,
    "category_external_id": p.category_external_id,
    "category_name": p.category_name,
    "raw_params_json": p.raw_params_json or {},
    "size_original": p.size_original,
    "color_original": p.color_original,
  }
