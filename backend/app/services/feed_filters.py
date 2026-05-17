from __future__ import annotations

from ..catalog_normalize import (
  letter_size_index,
  normalize_category,
  normalize_interest_category,
  normalize_size_token,
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
  # жёсткий потолок +5% на округление в фиде
  return price <= int(bmax * 1.05)


def product_passes_size(product: Product, fit: FitProfile | None) -> bool:
  """
  XL не видит товары, где в наличии только XS/S/M (макс. размер сильно меньше).
  Если размеров нет в карточке — пропускаем (данные неполные).
  """
  if fit is None:
    return True
  user_size = normalize_size_token((fit.clothing_size or "").strip())
  if not user_size:
    return True
  user_idx = letter_size_index(user_size)
  if user_idx is None:
    return True

  sizes = [
    normalize_size_token(str(s))
    for s in (product.available_sizes or [])
    if str(s).strip()
  ]
  if not sizes:
    return True

  upper = {s.upper() for s in sizes}
  if user_size.upper() in upper:
    return True

  indices = [letter_size_index(s) for s in sizes]
  letter_indices = [i for i in indices if i is not None]
  if not letter_indices:
    return True

  max_avail = max(letter_indices)
  # допускаем на один размер меньше (L видит M–XL), но не S-only для XL
  return max_avail >= user_idx - 1


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
