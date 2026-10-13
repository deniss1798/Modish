from __future__ import annotations

from ..catalog_normalize import (
  normalize_category,
  normalize_interest_category,
)
from ..models import FitProfile, Product
from .catalog.rule_filters import product_gender_compatible


def _product_category_norms(product: Product) -> set[str]:
  out: set[str] = set()
  for raw in (product.category, product.category_name, product.subcategory):
    c = normalize_category(str(raw or "").strip())
    if c:
      out.add(c)
  return out


def product_passes_budget(product: Product, fit: FitProfile | None) -> bool:
  if fit is None:
    return True
  bmax = int(fit.budget_max or 0)
  if bmax <= 0:
    return True
  price = int(product.price or 0)
  return 0 < price <= bmax


def product_passes_size(product: Product, fit: FitProfile | None) -> bool:
  return product_has_confirmed_size(product, fit)


def product_has_confirmed_size(product: Product, fit: FitProfile | None) -> bool:
  from .catalog.size_resolver import exact_size_match
  from .fit_size_profile import requested_size_for_product
  requested, system = requested_size_for_product(fit, product)
  return exact_size_match(product, requested, requested_system=system)


def product_passes_interest_categories(product: Product, fit: FitProfile | None) -> bool:
  if fit is None or not fit.interest_categories:
    return True
  interest_norm = {
    normalize_interest_category(str(x).strip())
    for x in fit.interest_categories
    if str(x).strip()
  }
  interest_norm.discard("")
  if not interest_norm:
    return True
  p_cats = _product_category_norms(product)
  if not p_cats:
    return False
  return bool(p_cats & interest_norm)


def product_passes_hard_filters(
  product: Product,
  fit: FitProfile | None,
) -> bool:
  user_gender = fit.gender_target if fit else None
  if not product_gender_compatible(product, user_gender):
    return False
  if not product_passes_budget(product, fit):
    return False
  if not product_passes_size(product, fit):
    return False
  if not product_passes_interest_categories(product, fit):
    return False
  return True
