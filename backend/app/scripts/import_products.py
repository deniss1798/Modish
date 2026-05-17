from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from ..catalog_normalize import (
  infer_size_system,
  normalize_category,
  normalize_colors_value,
  normalize_size_system,
  normalize_sizes,
)
from ..db import SessionLocal
from ..models import Product

_HEADER_ALIASES: dict[str, str] = {
  "id": "external_id",
  "sku": "external_id",
  "product_id": "external_id",
  "shop_sku": "external_id",

  "name": "title",
  "vendor": "brand",
  "brand": "brand",

  "model": "vendor_code",
  "vendorcode": "vendor_code",
  "vendor_code": "vendor_code",

  "categoryid": "category",
  "category_id": "category",
  "category": "category",

  "currencyid": "currency",
  "currency_id": "currency",
  "currency": "currency",

  "oldprice": "old_price",
  "old_price": "old_price",

  "url": "product_url",
  "link": "product_url",

  "image": "image_url",
  "picture": "image_url",
  "img": "image_url",
  "photo": "image_url",

  "barcode": "barcode",
  "available": "is_available",
  "description": "description",
}


def _norm_header(h: str | None) -> str:
  if h is None:
    return ""
  return str(h).strip().lower().replace(" ", "_").replace("-", "_")


def _parse_bool(raw: str | None, default: bool = True) -> bool:
  if raw is None or str(raw).strip() == "":
    return default
  s = str(raw).strip().lower()
  if s in {"1", "true", "yes", "y", "да"}:
    return True
  if s in {"0", "false", "no", "n", "нет"}:
    return False
  return default


def _parse_int(raw: str | None, default: int = 0) -> int:
  if raw is None or str(raw).strip() == "":
    return default
  try:
    return int(float(str(raw).strip().replace(",", ".")))
  except ValueError:
    return default


def _parse_float_opt(raw: str | None) -> float | None:
  if raw is None or str(raw).strip() == "":
    return None
  try:
    return float(str(raw).strip().replace(",", "."))
  except ValueError:
    return None


def _guess_csv_delimiter(path: Path) -> str:
  sample = path.read_text(encoding="utf-8-sig", errors="ignore")[:4096]
  if sample.count(";") > sample.count(","):
    return ";"
  return ","


def _row_to_item(row: dict[str, str]) -> dict[str, Any]:
  out: dict[str, Any] = {}

  for k, v in row.items():
    key = _norm_header(k)
    if not key:
      continue
    key = _HEADER_ALIASES.get(key, key)
    out[key] = (v or "").strip()

  if "sizes" not in out and "available_sizes" in out:
    out["sizes"] = out.pop("available_sizes")

  return out


def load_csv(path: Path) -> list[dict[str, Any]]:
  delimiter = _guess_csv_delimiter(path)

  with path.open(encoding="utf-8-sig", newline="") as f:
    reader = csv.DictReader(f, delimiter=delimiter)

    if not reader.fieldnames:
      raise SystemExit("CSV has no header row")

    items: list[dict[str, Any]] = []

    for row in reader:
      if not row or all(not (v or "").strip() for v in row.values()):
        continue

      base = _row_to_item({str(k): (v or "") for k, v in row.items() if k is not None})

      sizes_raw = base.pop("sizes", None) or base.pop("available_sizes", None)
      colors_raw = base.pop("colors", None)
      style_tags_raw = base.pop("style_tags", None)

      price = _parse_int(base.get("price"), 0)
      old_price = _parse_int(base.get("old_price"), 0) or None

      is_available = _parse_bool(base.get("is_available"), True)
      is_active = _parse_bool(base.get("is_active"), True)
      image_quality_score = _parse_float_opt(base.get("image_quality_score"))

      size_system = normalize_size_system(base.get("size_system"))
      sizes = normalize_sizes(sizes_raw, size_system=size_system)

      if not size_system and sizes:
        size_system = infer_size_system(sizes)

      style_tags: list[str] = []
      if isinstance(style_tags_raw, str):
        style_tags = [
          p.strip().lower()
          for p in style_tags_raw.replace(";", "|").split("|")
          if p.strip()
        ]
      elif isinstance(style_tags_raw, list):
        style_tags = [str(x).strip().lower() for x in style_tags_raw if str(x).strip()]

      colors = normalize_colors_value(colors_raw)

      detailed_raw = base.pop("available_sizes_detailed", None)
      sizes_detailed: list[dict[str, Any]] = []

      if detailed_raw:
        if isinstance(detailed_raw, str) and detailed_raw.strip().startswith("["):
          try:
            parsed = json.loads(detailed_raw)
            if isinstance(parsed, list):
              sizes_detailed = [dict(x) for x in parsed if isinstance(x, dict)]
          except json.JSONDecodeError:
            pass
        elif isinstance(detailed_raw, list):
          sizes_detailed = [dict(x) for x in detailed_raw if isinstance(x, dict)]

      item: dict[str, Any] = {
        "external_id": base.get("external_id") or base.get("id") or "",
        "source": base.get("source") or "import",
        "title": base.get("title") or "",
        "brand": base.get("brand") or "",
        "category": normalize_category(base.get("category") or ""),
        "subcategory": base.get("subcategory") or None,
        "price": price,
        "old_price": old_price,
        "currency": base.get("currency") or "RUB",
        "image_url": base.get("image_url") or "",
        "product_url": base.get("product_url") or "",
        "affiliate_url": base.get("affiliate_url") or base.get("product_url") or "",
        "original_url": base.get("original_url") or None,
        "description": base.get("description") or None,
        "barcode": base.get("barcode") or None,
        "vendor_code": base.get("vendor_code") or None,
        "sizes": sizes,
        "available_sizes_detailed": sizes_detailed,
        "size_system": size_system,
        "colors": colors,
        "color_family": base.get("color_family") or None,
        "material": base.get("material") or None,
        "season": base.get("season") or None,
        "occasion": base.get("occasion") or None,
        "gender_target": base.get("gender_target") or None,
        "fit": base.get("fit") or None,
        "silhouette": base.get("silhouette") or None,
        "style_tags": style_tags,
        "image_quality_score": image_quality_score,
        "is_available": is_available,
        "is_active": is_active,
        "feed_raw_json": dict(base),
      }

      items.append(item)

    return items


def load_items(path: Path) -> list[dict[str, Any]]:
  data = json.loads(path.read_text(encoding="utf-8"))

  if isinstance(data, dict) and isinstance(data.get("items"), list):
    raw_list = [dict(x) for x in data["items"]]
  elif isinstance(data, list):
    raw_list = [dict(x) for x in data]
  else:
    raise SystemExit("Invalid JSON format: expected list or {items: [...]}")

  return [_json_item_normalized(x) for x in raw_list]


def _json_item_normalized(it: dict[str, Any]) -> dict[str, Any]:
  out = dict(it)

  out["category"] = normalize_category(str(it.get("category") or "").strip())

  size_system = normalize_size_system(it.get("size_system"))
  sizes = normalize_sizes(it.get("sizes") or it.get("available_sizes"), size_system=size_system)

  if not size_system and sizes:
    size_system = infer_size_system(sizes)

  out["size_system"] = size_system
  out["sizes"] = sizes
  out["colors"] = normalize_colors_value(it.get("colors"))

  return out


def upsert_products(items: list[dict[str, Any]], *, source: str | None = None) -> tuple[int, int]:
  created = 0
  updated = 0
  skipped = 0
  now = datetime.now(timezone.utc)

  with SessionLocal() as db:
    for it in items:
      src = str(source or it.get("source") or "import").strip()

      title = str(it.get("title") or "").strip()
      brand = str(it.get("brand") or src or "unknown").strip()
      category = normalize_category(str(it.get("category") or "").strip())
      price = int(it.get("price") or 0)
      image_url = str(it.get("image_url") or "").strip()
      product_url = str(it.get("product_url") or "").strip()
      affiliate_url = str(it.get("affiliate_url") or product_url).strip()

      if not (title and category and image_url and product_url and price > 0):
        skipped += 1
        continue

      external_id = str(
        it.get("external_id")
        or it.get("vendor_code")
        or it.get("barcode")
        or f"{brand}:{title}:{price}"
      ).strip()

      if not external_id:
        skipped += 1
        continue

      size_system = normalize_size_system(it.get("size_system"))
      sizes = normalize_sizes(it.get("sizes") or it.get("available_sizes"), size_system=size_system)

      if not size_system and sizes:
        size_system = infer_size_system(sizes)

      colors = normalize_colors_value(it.get("colors"))

      sizes_detailed = it.get("available_sizes_detailed") or []
      if not isinstance(sizes_detailed, list):
        sizes_detailed = []

      style_tags = [
        str(x).strip().lower()
        for x in (it.get("style_tags") or [])
        if str(x).strip()
      ]

      old_price = it.get("old_price")
      old_price_int = int(old_price) if old_price else None

      discount_percent = None
      if old_price_int and old_price_int > price > 0:
        discount_percent = int(round((old_price_int - price) / old_price_int * 100))

      existing = db.execute(
        select(Product).where(Product.external_id == external_id, Product.source == src)
      ).scalar_one_or_none()

      payload = {
        "external_id": external_id,
        "source": src,
        "title": title,
        "brand": brand,
        "category": category,
        "subcategory": str(it.get("subcategory")).strip() if it.get("subcategory") else None,
        "price": price,
        "currency": str(it.get("currency") or "RUB").strip() or "RUB",
        "old_price": old_price_int,
        "discount_percent": discount_percent,
        "availability_status": "available" if it.get("is_available", True) else "unavailable",
        "image_url": image_url,
        "product_url": product_url,
        "affiliate_url": affiliate_url,
        "original_url": str(it.get("original_url")).strip() if it.get("original_url") else None,
        "description": str(it.get("description")).strip() if it.get("description") else None,
        "barcode": str(it.get("barcode")).strip() if it.get("barcode") else None,
        "vendor_code": str(it.get("vendor_code")).strip() if it.get("vendor_code") else None,
        "available_sizes": sizes,
        "available_sizes_detailed": [dict(x) for x in sizes_detailed if isinstance(x, dict)],
        "size_system": size_system,
        "colors": colors,
        "color_family": str(it.get("color_family")).strip() if it.get("color_family") else None,
        "material": str(it.get("material")).strip() if it.get("material") else None,
        "season": str(it.get("season")).strip() if it.get("season") else None,
        "occasion": str(it.get("occasion")).strip() if it.get("occasion") else None,
        "gender_target": str(it.get("gender_target")).strip() if it.get("gender_target") else None,
        "fit": str(it.get("fit")).strip() if it.get("fit") else None,
        "silhouette": str(it.get("silhouette")).strip() if it.get("silhouette") else None,
        "style_tags": style_tags,
        "image_quality_score": float(it.get("image_quality_score")) if it.get("image_quality_score") is not None else None,
        "feed_raw_json": it.get("feed_raw_json") if isinstance(it.get("feed_raw_json"), dict) else dict(it),
        "is_available": 1 if it.get("is_available", True) else 0,
        "last_checked_at": now,
        "last_seen_in_feed_at": now,
        "is_active": 1 if it.get("is_active", True) else 0,
        "updated_at": now,
      }

      if existing is None:
        p = Product(
          id=str(uuid4()),
          created_at=now,
          **payload,
        )
        db.add(p)
        created += 1
      else:
        for key, value in payload.items():
          setattr(existing, key, value)
        updated += 1

    db.commit()

  print(f"Skipped products: {skipped}")
  return created, updated


def load_file(path: Path) -> list[dict[str, Any]]:
  suffix = path.suffix.lower()

  if suffix == ".csv":
    return load_csv(path)

  if suffix == ".json":
    return load_items(path)

  raise SystemExit(f"Unsupported file type: {suffix} (use .csv or .json)")


def main() -> None:
  parser = argparse.ArgumentParser(description="Import products from JSON or CSV into Modish DB.")
  parser.add_argument("path", help="Path to .json/list or .csv")
  parser.add_argument("--source", default=None, help="Override source field for all items")

  args = parser.parse_args()

  path = Path(args.path)

  if not path.is_file():
    raise SystemExit(f"File not found: {path}")

  items = load_file(path)
  created, updated = upsert_products(items, source=args.source)

  print(f"Imported products: created={created} updated={updated} from={path}")


if __name__ == "__main__":
  main()