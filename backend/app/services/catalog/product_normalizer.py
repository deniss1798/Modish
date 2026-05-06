from __future__ import annotations

from datetime import datetime
from typing import Any

from ...catalog_normalize import normalize_category, normalize_product_colors
from .normalized_product import NormalizedProduct


def normalized_to_dict(n: NormalizedProduct) -> dict[str, Any]:
  """Поля для ORM Product / upsert (без id)."""
  cat = normalize_category(n.category)
  return {
    "external_id": n.external_id,
    "source": n.source_code,
    "title": n.title[:255],
    "brand": (n.brand or "")[:128],
    "category": cat,
    "subcategory": (n.subcategory or "")[:64] if n.subcategory else None,
    "price": int(n.price),
    "old_price": n.old_price,
    "discount_percent": n.discount_percent,
    "currency": (n.currency or "RUB")[:8],
    "availability_status": n.availability_status,
    "image_url": n.image_url,
    "product_url": n.original_url or n.affiliate_url or "",
    "affiliate_url": n.affiliate_url,
    "original_url": n.original_url or None,
    "available_sizes": n.sizes,
    "colors": normalize_product_colors(n.colors),
    "gender_target": n.gender_target,
    "material": n.material,
    "season": n.season,
    "style_tags": n.style_tags,
    "merchant_category": n.merchant_category,
    "merchant_subcategory": n.merchant_subcategory,
    "feed_raw_json": n.raw_data or {},
  }


def orm_kwargs_from_normalized(
  n: NormalizedProduct,
  *,
  source_id: str,
  sync_ts: datetime,
) -> dict[str, Any]:
  """Поля для создания/обновления Product (без id)."""
  payload = normalized_to_dict(n)
  raw_json = dict(payload.get("feed_raw_json") or {})
  raw_json.pop("_modish_available_hint", None)
  payload["feed_raw_json"] = raw_json
  hint = True
  if isinstance(n.raw_data, dict) and "_modish_available_hint" in n.raw_data:
    hint = bool(n.raw_data.get("_modish_available_hint", True))
  avail_ok = hint and (n.availability_status != "out_of_stock")
  payload["is_available"] = 1 if avail_ok else 0
  payload["is_deleted_from_feed"] = 0
  payload["is_active"] = 1
  payload["source_id"] = source_id
  payload["last_seen_in_feed_at"] = sync_ts
  return payload
