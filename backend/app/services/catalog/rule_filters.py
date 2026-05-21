from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...catalog_normalize import infer_gender_from_text, product_gender_from_model, resolve_product_gender
from ...models import Product, SourceRule


def load_rules_by_source_id(db: Session) -> dict[str, list]:
  rows = db.execute(select(SourceRule).where(SourceRule.is_active == 1)).scalars().all()
  out: dict[str, list] = defaultdict(list)
  for r in rows:
    out[r.source_id].append(r)
  return out


def _haystack(product: Product) -> str:
  return f"{product.title or ''} {product.brand or ''}".lower()


def product_passes_source_rules(product: Product, rules_by_source_id: dict[str, list]) -> bool:
  if not product.source_id:
    return True
  rules = rules_by_source_id.get(product.source_id, [])
  allowed_categories: set[str] = set()
  has_allowed_rule = False
  for r in rules:
    if not r.is_active:
      continue
    rt = (r.rule_type or "").strip()
    rv = (r.rule_value or "").strip()
    if rt == "allowed_category":
      has_allowed_rule = True
      allowed_categories.add(normalize_category(rv))
      continue
    if rt == "blocked_brand" and rv.lower() == (product.brand or "").strip().lower():
      return False
    if rt == "min_price":
      try:
        if int(product.price) < int(rv):
          return False
      except ValueError:
        pass
      continue
    if rt == "blocked_category":
      if rv.lower() in (product.category or "").lower():
        return False
      continue
    if rt == "blocked_category_exact" and rv.lower() == (product.category or "").strip().lower():
      return False
    if rt == "blocked_keyword" and rv and rv.lower() in _haystack(product):
      return False
    if rt == "requires_affiliate_url":
      au = (product.affiliate_url or "").strip()
      if not au:
        return False
      continue
    if rt == "hide_without_image":
      img = (product.image_url or "").strip()
      if not img:
        return False
      continue
    if rt == "hide_without_size":
      sizes = product.available_sizes or []
      if not sizes or not any(str(s).strip() for s in sizes):
        return False
      continue
  if has_allowed_rule:
    pc = normalize_category(product.category or "")
    if pc not in allowed_categories:
      return False
  return True


def product_gender_compatible(product: Product, user_gender: str | None) -> bool:
  """user_gender: fit_profiles.gender_target (menswear / womenswear / unisex)."""
  ug = (user_gender or "").strip().lower()
  if not ug or ug in ("unisex", "unknown"):
    return True

  pg = product_gender_from_model(product)
  if pg == ug:
    return True
  if pg == "unisex":
    return True
  if pg and pg != ug:
    return False

  # gender не задан в каталоге — эвристика по названию/категории
  inferred = resolve_product_gender(
    gender_target=None,
    title=product.title or "",
    category=product.category or "",
    category_name=product.category_name or "",
    merchant_category=product.merchant_category or "",
  )
  if inferred and inferred != "unisex" and inferred != ug:
    return False

  # Явные маркеры противоположного пола в названии
  hay = f"{product.title or ''} {product.category_name or ''} {product.category or ''}".lower()
  if ug == "menswear":
    if any(h in hay for h in _FEMALE_ONLY_HAYSTACK):
      return False
  if ug == "womenswear":
    if any(h in hay for h in _MALE_ONLY_HAYSTACK):
      return False
  return True


_FEMALE_ONLY_HAYSTACK = (
  "женск",
  "для женщин",
  "women",
  "womens",
  "ladies",
  "платье",
  "юбка",
  "блуз",
  "лиф",
)

_MALE_ONLY_HAYSTACK = (
  "мужск",
  "для мужчин",
  " mens ",
  "men's",
  "menswear",
)
