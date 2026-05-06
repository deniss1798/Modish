from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import Product, SourceRule


def load_rules_by_source_id(db: Session) -> dict[str, list]:
  rows = db.execute(select(SourceRule).where(SourceRule.is_active == 1)).scalars().all()
  out: dict[str, list] = defaultdict(list)
  for r in rows:
    out[r.source_id].append(r)
  return out


def product_passes_source_rules(product: Product, rules_by_source_id: dict[str, list]) -> bool:
  if not product.source_id:
    return True
  for r in rules_by_source_id.get(product.source_id, []):
    if not r.is_active:
      continue
    rt = (r.rule_type or "").strip()
    rv = (r.rule_value or "").strip()
    if rt == "blocked_brand" and rv.lower() == (product.brand or "").strip().lower():
      return False
    if rt == "min_price":
      try:
        if int(product.price) < int(rv):
          return False
      except ValueError:
        pass
    if rt == "blocked_category":
      if rv.lower() in (product.category or "").lower():
        return False
    if rt == "blocked_category_exact" and rv.lower() == (product.category or "").strip().lower():
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
