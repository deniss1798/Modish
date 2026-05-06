from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...catalog_normalize import normalize_category
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
  """user_gender: fit_profiles.gender_target."""
  if not user_gender or user_gender in ("unisex", "unknown"):
    return True
  gt = (product.gender_target or "").strip().lower()
  if not gt or gt == "unisex":
    return True
  return gt == user_gender.strip().lower()
