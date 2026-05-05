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
  "url": "product_url",
  "link": "product_url",
  "image": "image_url",
  "picture": "image_url",
  "img": "image_url",
  "photo": "image_url",
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
    return int(float(str(raw).strip()))
  except ValueError:
    return default


def _parse_float_opt(raw: str | None) -> float | None:
  if raw is None or str(raw).strip() == "":
    return None
  try:
    return float(str(raw).strip().replace(",", "."))
  except ValueError:
    return None


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
  with path.open(encoding="utf-8-sig", newline="") as f:
    reader = csv.DictReader(f)
    if not reader.fieldnames:
      raise SystemExit("CSV has no header row")
    items: list[dict[str, Any]] = []
    for row in reader:
      if not row or all(not (v or "").strip() for v in row.values()):
        continue
      base = _row_to_item({str(k): (v or "") for k, v in row.items() if k is not None})
      sizes_raw = base.pop("sizes", None) or base.pop("available_sizes", None)
      colors_raw = base.pop("colors", None)
      st_raw = base.pop("style_tags", None)
      price = _parse_int(base.get("price"), 0)
      is_avail = _parse_bool(base.get("is_available"), True)
      is_active = _parse_bool(base.get("is_active"), True)
      iq = _parse_float_opt(base.get("image_quality_score"))

      size_system = normalize_size_system(base.get("size_system"))
      sizes = normalize_sizes(sizes_raw, size_system=size_system)
      if not size_system and sizes:
        size_system = infer_size_system(sizes)

      style_tags: list[str] = []
      if isinstance(st_raw, str):
        style_tags = [p.strip().lower() for p in st_raw.replace(";", "|").split("|") if p.strip()]
      elif isinstance(st_raw, list):
        style_tags = [str(x).strip().lower() for x in st_raw if str(x).strip()]

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
        "currency": base.get("currency") or "RUB",
        "image_url": base.get("image_url") or "",
        "product_url": base.get("product_url") or "",
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
        "image_quality_score": iq,
        "is_available": is_avail,
        "is_active": is_active,
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
  sys = normalize_size_system(it.get("size_system"))
  sizes = normalize_sizes(it.get("sizes") or it.get("available_sizes"), size_system=sys)
  if not sys and sizes:
    sys = infer_size_system(sizes)
  out["size_system"] = sys
  out["sizes"] = sizes
  out["colors"] = normalize_colors_value(it.get("colors"))
  return out


def upsert_products(items: list[dict[str, Any]], *, source: str | None = None) -> tuple[int, int]:
  created = 0
  updated = 0
  now = datetime.now(timezone.utc)
  with SessionLocal() as db:
    for it in items:
      title = str(it.get("title") or "").strip()
      brand = str(it.get("brand") or "").strip()
      category = normalize_category(str(it.get("category") or "").strip())
      price = int(it.get("price") or 0)
      image_url = str(it.get("image_url") or "").strip()
      product_url = str(it.get("product_url") or "").strip()
      if not (title and brand and category and image_url and product_url):
        continue

      src = str(source or it.get("source") or "import").strip()
      external_id = str(it.get("external_id") or it.get("id") or f"{brand}:{title}:{price}").strip()
      sys = normalize_size_system(it.get("size_system"))
      sizes = normalize_sizes(it.get("sizes") or it.get("available_sizes"), size_system=sys)
      if not sys and sizes:
        sys = infer_size_system(sizes)
      colors = normalize_colors_value(it.get("colors"))
      sizes_detailed = it.get("available_sizes_detailed") or []
      if not isinstance(sizes_detailed, list):
        sizes_detailed = []
      style_tags = [str(x).strip().lower() for x in (it.get("style_tags") or []) if str(x).strip()]

      existing = db.execute(
        select(Product).where(Product.external_id == external_id, Product.source == src)
      ).scalar_one_or_none()

      if existing is None:
        p = Product(
          id=str(uuid4()),
          external_id=external_id,
          source=src,
          title=title,
          brand=brand,
          category=category,
          subcategory=str(it.get("subcategory")).strip() if it.get("subcategory") else None,
          price=price,
          currency=str(it.get("currency") or "RUB").strip() or "RUB",
          image_url=image_url,
          product_url=product_url,
          available_sizes=sizes,
          available_sizes_detailed=[dict(x) for x in sizes_detailed if isinstance(x, dict)],
          size_system=sys,
          colors=colors,
          color_family=str(it.get("color_family")).strip() if it.get("color_family") else None,
          material=str(it.get("material")).strip() if it.get("material") else None,
          season=str(it.get("season")).strip() if it.get("season") else None,
          occasion=str(it.get("occasion")).strip() if it.get("occasion") else None,
          gender_target=str(it.get("gender_target")).strip() if it.get("gender_target") else None,
          fit=str(it.get("fit")).strip() if it.get("fit") else None,
          silhouette=str(it.get("silhouette")).strip() if it.get("silhouette") else None,
          style_tags=style_tags,
          image_quality_score=float(it.get("image_quality_score")) if it.get("image_quality_score") is not None else None,
          is_available=1 if it.get("is_available", True) else 0,
          last_checked_at=now,
          is_active=1 if it.get("is_active", True) else 0,
          created_at=now,
          updated_at=now,
        )
        db.add(p)
        created += 1
      else:
        existing.title = title
        existing.brand = brand
        existing.category = category
        existing.subcategory = str(it.get("subcategory")).strip() if it.get("subcategory") else None
        existing.price = price
        existing.currency = str(it.get("currency") or existing.currency or "RUB").strip() or "RUB"
        existing.image_url = image_url
        existing.product_url = product_url
        existing.available_sizes = sizes
        existing.colors = colors
        existing.available_sizes_detailed = [dict(x) for x in sizes_detailed if isinstance(x, dict)]
        existing.size_system = sys
        existing.color_family = str(it.get("color_family")).strip() if it.get("color_family") else None
        existing.material = str(it.get("material")).strip() if it.get("material") else None
        existing.season = str(it.get("season")).strip() if it.get("season") else None
        existing.occasion = str(it.get("occasion")).strip() if it.get("occasion") else None
        existing.gender_target = str(it.get("gender_target")).strip() if it.get("gender_target") else None
        existing.fit = str(it.get("fit")).strip() if it.get("fit") else None
        existing.silhouette = str(it.get("silhouette")).strip() if it.get("silhouette") else None
        existing.style_tags = style_tags
        existing.image_quality_score = float(it.get("image_quality_score")) if it.get("image_quality_score") is not None else None
        existing.is_available = 1 if it.get("is_available", True) else 0
        existing.last_checked_at = now
        existing.is_active = 1 if it.get("is_active", True) else 0
        existing.updated_at = now
        updated += 1
    db.commit()
  return created, updated


def load_file(path: Path) -> list[dict[str, Any]]:
  suf = path.suffix.lower()
  if suf == ".csv":
    return load_csv(path)
  if suf == ".json":
    return load_items(path)
  raise SystemExit(f"Unsupported file type: {suf} (use .csv or .json)")


def main() -> None:
  ap = argparse.ArgumentParser(description="Import products from JSON or CSV into Modish DB.")
  ap.add_argument("path", help="Path to .json (list or {items:[...]}) or .csv")
  ap.add_argument("--source", default=None, help="Override 'source' field for all items")
  args = ap.parse_args()

  path = Path(args.path)
  if not path.is_file():
    raise SystemExit(f"File not found: {path}")

  items = load_file(path)
  c, u = upsert_products(items, source=args.source)
  print(f"Imported products: created={c} updated={u} from={path}")


if __name__ == "__main__":
  main()
