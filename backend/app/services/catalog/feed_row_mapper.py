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
  if raw is None:
    return []
  if isinstance(raw, list):
    return [str(x).strip() for x in raw if str(x).strip()]
  parts = re.split(r"[,;/|]", str(raw))
  return [p.strip() for p in parts if p.strip()]


def _normalize_media_url(raw: Any) -> str:
  """Протокол-относительные //host → https:// ; обрезка."""
  s = str(raw or "").strip()
  if not s:
    return ""
  if s.startswith("//"):
    return f"https:{s}"
  return s


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
  cat_id_raw = _pick(d, "categoryid", "category_id")
  category_external_id = str(cat_id_raw).strip()[:64] if cat_id_raw else None
  cat_name_pick = _pick(d, "category_name")
  category_name_val = str(cat_name_pick).strip()[:255] if cat_name_pick else None
  category = (
    str(
      category_name_val
      or category_external_id
      or _pick(d, "category", "type", "section")
      or "разное",
    ).strip()
    or "разное"
  )
  sub = _pick(d, "subcategory", "sub_category", "typeprefix", "type_prefix")
  sub_s = str(sub).strip()[:128] if sub else None

  price = _int_price(_pick(d, "price", "local_price", "current_price")) or 0
  old_price = _int_price(_pick(d, "oldprice", "old_price", "price_old", "baseprice", "base_price"))
  currency = str(_pick(d, "currencyid", "currency_id", "currency") or "RUB")[:8]

  raw_img = _pick(d, "picture", "image", "image_url", "img", "thumbnail", "photo")
  if isinstance(raw_img, list):
    img = _normalize_media_url(raw_img[0]) if raw_img else ""
  else:
    img = _normalize_media_url(raw_img)

  pics_src = d.get("pictures")
  image_urls: list[str] = []
  if isinstance(pics_src, list):
    for x in pics_src:
      u = _normalize_media_url(x)
      if u:
        image_urls.append(u)
      if len(image_urls) >= 80:
        break
  if not img and image_urls:
    img = image_urls[0]

  url = str(_pick(d, "url", "product_url", "link", "deeplink", "available_url") or "").strip()
  affiliate = str(_pick(d, "affiliate_url", "partner_link", "admitad_url", "gotolink", "goto_link") or "").strip()

  sizes = _split_sizes(
    _pick(
      d,
      "sizes",
      "size",
      "available_sizes",
      "param_sizes",
      "param_размер",
      "param_size",
    )
  )
  colors = _split_colors(
    _pick(
      d,
      "color",
      "colors",
      "colour",
      "param_цвет",
      "param_color",
      "param_colour",
    )
  )

  gender = _pick(d, "gender", "sex", "target_gender", "param_пол")
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

  raw_params: dict[str, Any] = {}
  for rk, rv in d.items():
    if not str(rk).startswith("param_"):
      continue
    if rv is None or str(rv).strip() == "":
      continue
    raw_params[str(rk)] = rv

  desc_raw = _pick(d, "description")
  if desc_raw is None:
    description = None
  else:
    description = str(desc_raw).strip()[:32000] or None

  gid = _pick(d, "group_id")
  group_id = str(gid).strip()[:64] if gid else None
  if not group_id:
    cids = d.get("collectionids")
    if isinstance(cids, list) and cids:
      group_id = str(cids[0]).strip()[:64] or None
    elif cids:
      group_id = str(cids).strip()[:64] or None

  barcode = str(_pick(d, "barcode") or "").strip()[:128] or None
  vendor_code = str(_pick(d, "vendorcode", "vendor_code") or "").strip()[:128] or None

  sz_o = _pick(d, "param_размер", "param_size")
  size_original = str(sz_o).strip()[:64] if sz_o else None
  c_o = _pick(d, "param_цвет", "param_color", "param_colour")
  color_original = str(c_o).strip()[:128] if c_o else None

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
    group_id=group_id,
    description=description,
    image_urls=image_urls,
    barcode=barcode,
    vendor_code=vendor_code,
    category_external_id=category_external_id,
    category_name=category_name_val,
    raw_params=raw_params,
    size_original=size_original,
    color_original=color_original,
  )
  # сохраняем флаг для импортера
  n.raw_data["_modish_available_hint"] = is_likely_available
  return n
