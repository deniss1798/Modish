from __future__ import annotations

import re
from typing import Any

from ...models import ProductSource
from .normalized_product import NormalizedProduct


def _lower_key_map(row: dict[str, Any]) -> dict[str, Any]:
  out: dict[str, Any] = {}
  for k, v in row.items():
    if k is None:
      continue
    key = str(k).strip().lower().replace(" ", "_")
    out[key] = v
  return out


def _pick(d: dict[str, Any], *keys: str) -> Any:
  for k in keys:
    if k in d and d[k] is not None and str(d[k]).strip() != "":
      return d[k]
  return None


def _int_price(raw: Any) -> int | None:
  if raw is None:
    return None
  if isinstance(raw, (int, float)):
    return int(raw)
  s = re.sub(r"[^\d]", "", str(raw))
  if not s:
    return None
  try:
    return int(s)
  except ValueError:
    return None


def _split_sizes(raw: Any) -> list[str]:
  if raw is None:
    return []
  if isinstance(raw, list):
    return [str(x).strip() for x in raw if str(x).strip()]
  parts = re.split(r"[,;/|]", str(raw))
  return [p.strip() for p in parts if p.strip()]


def _split_colors(raw: Any) -> list[str]:
  return _split_sizes(raw)


def row_to_normalized(row: dict[str, Any], source: ProductSource) -> NormalizedProduct | None:
  """
  Универсальный маппинг типичных полей Admitad / YML / XML / CSV.
  Ключи приводятся к lower_snake_case.
  """
  d = _lower_key_map(row)
  external_id = _pick(
    d,
    "id",
    "offer_id",
    "external_id",
    "product_id",
    "vendorcode",
    "vendor_code",
    "sku",
    "g_id",
  )
  if external_id is None:
    return None
  eid = str(external_id).strip()[:128]
  if not eid:
    return None

  title = str(_pick(d, "name", "title", "model", "product_name") or "Товар")[:500]
  brand = str(_pick(d, "vendor", "brand", "manufacturer") or "")[:128]
  category = str(_pick(d, "categoryid", "category_id", "category", "type", "section") or "разное")
  sub = _pick(d, "subcategory", "sub_category", "typeprefix", "type_prefix")
  sub_s = str(sub).strip()[:128] if sub else None

  price = _int_price(_pick(d, "price", "local_price", "current_price")) or 0
  old_price = _int_price(_pick(d, "oldprice", "old_price", "price_old", "baseprice", "base_price"))
  currency = str(_pick(d, "currencyid", "currency_id", "currency") or "RUB")[:8]

  img = str(_pick(d, "picture", "image", "image_url", "img", "thumbnail", "photo") or "").strip()
  url = str(_pick(d, "url", "product_url", "link", "deeplink", "available_url") or "").strip()
  affiliate = str(_pick(d, "affiliate_url", "partner_link", "admitad_url", "gotolink", "goto_link") or "").strip()

  sizes = _split_sizes(_pick(d, "sizes", "size", "available_sizes", "param_sizes"))
  colors = _split_colors(_pick(d, "color", "colors", "colour"))

  gender = _pick(d, "gender", "sex", "target_gender")
  gender_s = str(gender).strip().lower()[:32] if gender else None
  if gender_s in ("male", "m", "мужской"):
    gender_s = "menswear"
  elif gender_s in ("female", "f", "женский"):
    gender_s = "womenswear"

  avail = str(_pick(d, "available", "availability", "stock", "instock", "in_stock") or "").lower()
  availability_status = None
  is_likely_available = True
  if avail in ("false", "0", "no", "out", "out_of_stock", "нет"):
    availability_status = "out_of_stock"
    is_likely_available = False

  discount = _int_price(_pick(d, "discount", "discount_percent", "sale_percent"))

  raw_copy: dict[str, Any] = {str(k): v for k, v in row.items()}

  n = NormalizedProduct(
    external_id=eid,
    source_code=source.code,
    title=title,
    brand=brand,
    category=category,
    subcategory=sub_s,
    price=max(0, price),
    old_price=old_price if old_price and old_price > price else None,
    currency=currency,
    image_url=img,
    original_url=url or affiliate,
    affiliate_url=affiliate or None,
    sizes=sizes,
    colors=colors,
    gender_target=gender_s,
    material=str(_pick(d, "material", "fabric") or "")[:64] or None,
    season=str(_pick(d, "season", "collection") or "")[:32] or None,
    style_tags=[],
    raw_data=raw_copy,
    discount_percent=discount,
    availability_status=availability_status,
    merchant_category=str(_pick(d, "merchant_category", "google_product_category") or "")[:128] or None,
    merchant_subcategory=str(_pick(d, "merchant_subcategory") or "")[:128] or None,
  )
  # сохраняем флаг для импортера
  n.raw_data["_modish_available_hint"] = is_likely_available
  return n
