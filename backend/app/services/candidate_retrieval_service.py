"""Candidate Retrieval v2 for MIE.

Builds a deduplicated candidate pool from multiple full-catalog-capable sources
before ranking.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy import String, and_, cast, desc, func, or_, select
from sqlalchemy.orm import Session

from ..catalog_normalize import letter_size_index, normalize_category, normalize_size_token
from ..models import FitProfile, Product, RecommendationEventV2, TasteProfile, User
from .embedding_service import retrieve_embedding_candidates
from .mie_scoring import TasteFeatureMap
from .catalog_filters import CatalogFilters
from .catalog.rule_filters import product_gender_compatible
from .catalog_audience import adult_catalog_conditions, product_is_adult
from .catalog_scope import fashion_catalog_conditions, product_is_fashion


SCENARIO_CATEGORIES: dict[str, set[str]] = {
  "daily": {"джинсы", "брюки", "футболки", "рубашки", "джемперы", "обувь"},
  "office": {"рубашки", "брюки", "костюмы", "джемперы", "обувь", "сумки"},
  "date": {"платья", "рубашки", "брюки", "юбки", "обувь", "аксессуары"},
  "restaurant": {"платья", "рубашки", "брюки", "костюмы", "обувь", "аксессуары"},
  "wedding": {"платья", "костюмы", "рубашки", "обувь", "аксессуары"},
  "gym": {"спорт", "футболки", "шорты", "обувь"},
  "run": {"спорт", "футболки", "шорты", "обувь"},
  "university": {"джинсы", "футболки", "рубашки", "худи", "рюкзаки", "обувь"},
  "party": {"платья", "юбки", "рубашки", "обувь", "аксессуары"},
  "vacation": {"платья", "шорты", "футболки", "купальники", "обувь", "сумки"},
}

LETTER_SIZES = ("XXS", "XS", "S", "M", "L", "XL", "XXL", "XXXL")


@dataclass
class CandidateRetrievalResult:
  products: list[Product]
  sources_by_product_id: dict[str, set[str]] = field(default_factory=dict)


def _base_conditions(
  *,
  source: str | None,
  exclude_product_ids: set[str],
  min_price: int,
) -> list:
  conds = [
    *adult_catalog_conditions(),
    *fashion_catalog_conditions(),
    Product.is_active == 1,
    Product.is_available == 1,
    Product.is_deleted_from_feed == 0,
    Product.source != "demo",
    Product.price > min_price,
    Product.image_url.isnot(None),
    Product.image_url != "",
    or_(
      and_(Product.affiliate_url.isnot(None), Product.affiliate_url != ""),
      Product.product_url != "",
    ),
  ]
  if source and source.strip():
    conds.append(Product.source == source.strip())
  if exclude_product_ids:
    conds.append(Product.id.notin_(list(exclude_product_ids)))
  return conds


def _ordered_recent(query):
  return query.order_by(
    Product.external_updated_at.desc(),
    Product.last_seen_in_feed_at.desc(),
    Product.updated_at.desc(),
    Product.created_at.desc(),
    Product.id.asc(),
  )


def _positive_feature_values(taste_features: TasteFeatureMap) -> dict[str, set[str]]:
  out: dict[str, set[str]] = {}
  for (feature_type, feature_value), (preference, confidence) in taste_features.items():
    if preference <= 0 or confidence < 0.15:
      continue
    out.setdefault(feature_type, set()).add(feature_value)
  return out


def _legacy_positive_values(taste: TasteProfile) -> dict[str, set[str]]:
  source = {
    "category": taste.category_weights or {},
    "brand": taste.brand_weights or {},
    "color": taste.color_weights or {},
    "style": taste.style_weights or {},
  }
  out: dict[str, set[str]] = {}
  for feature_type, weights in source.items():
    if not isinstance(weights, dict):
      continue
    for raw, value in weights.items():
      try:
        weight = float(value)
      except Exception:
        continue
      if weight > 0:
        out.setdefault(feature_type, set()).add(str(raw).strip().lower())
  return out


def _merge_feature_values(*maps: dict[str, set[str]]) -> dict[str, set[str]]:
  out: dict[str, set[str]] = {}
  for values in maps:
    for key, items in values.items():
      out.setdefault(key, set()).update(items)
  return out


def _fit_gender_condition(fit: FitProfile | None):
  if fit is None:
    return None
  gender = (fit.gender_target or "").strip().lower()
  if gender not in {"menswear", "womenswear"}:
    return None
  # Existing rows are repaired by scripts/repair_catalog_gender.py at release.
  # Imports persist the same resolver's result. Keep this indexed gate cheap;
  # authoritative merchant metadata is checked again before returning a card.
  return Product.gender_target.in_([gender, "unisex"])



def _norm_values(values: set[str]) -> set[str]:
  return {str(v).strip().lower() for v in values if str(v).strip()}


def _like_token(value: str) -> str:
  return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_").replace('"', '\\"')


def _json_list_contains_any(column, values: set[str]):
  vals = _norm_values(values)
  if not vals:
    return None
  text = func.lower(cast(column, String))
  return or_(*[text.like(f'%"{_like_token(v)}"%', escape="\\") for v in sorted(vals)])


def _json_list_empty_or_contains_any(column, values: set[str]):
  contains = _json_list_contains_any(column, values)
  if contains is None:
    return None
  text = func.lower(cast(column, String))
  return or_(column.is_(None), text.in_(["[]", "null"]), contains)


def _price_band_condition(price_bands: set[str]):
  bands = _norm_values(price_bands)
  conds = []
  if "budget" in bands:
    conds.append(and_(Product.price > 0, Product.price < 2_000))
  if "mid" in bands:
    conds.append(and_(Product.price >= 2_000, Product.price < 6_000))
  if "premium" in bands:
    conds.append(and_(Product.price >= 6_000, Product.price < 15_000))
  if "luxury" in bands:
    conds.append(Product.price >= 15_000)
  return or_(*conds) if conds else None


def _fit_budget_condition(fit: FitProfile | None):
  if fit is None:
    return None
  budget_max = int(fit.budget_max or 0)
  if budget_max <= 0:
    return None
  return Product.price <= int(budget_max * 1.05)


def _fit_size_condition(fit: FitProfile | None):
  if fit is None:
    return None
  user_size = normalize_size_token((fit.clothing_size or "").strip())
  user_idx = letter_size_index(user_size)
  if user_idx is None:
    return None
  acceptable = {size for size in LETTER_SIZES if (letter_size_index(size) or -1) >= user_idx - 1}
  return _json_list_empty_or_contains_any(Product.available_sizes, acceptable)


def _add_rows(
  result: CandidateRetrievalResult,
  rows: Iterable[Product],
  *,
  source_name: str,
  max_candidates: int,
) -> None:
  if len(result.products) >= max_candidates:
    return
  seen = {p.id for p in result.products}
  for product in rows:
    if not product or not product.id:
      continue
    if not product_is_adult(product) or not product_is_fashion(product):
      continue
    result.sources_by_product_id.setdefault(product.id, set()).add(source_name)
    if product.id in seen:
      continue
    if len(result.products) >= max_candidates:
      return
    result.products.append(product)
    seen.add(product.id)


def retrieve_candidates(
  db: Session,
  *,
  user: User,
  fit: FitProfile | None,
  taste: TasteProfile,
  taste_features: TasteFeatureMap,
  exclude_product_ids: set[str] | None = None,
  source: str | None = None,
  scenario: str = "daily",
  max_candidates: int = 700,
  min_price: int = 500,
  filters: CatalogFilters | None = None,
) -> CandidateRetrievalResult:
  exclude = {str(pid) for pid in (exclude_product_ids or set()) if str(pid).strip()}
  base = _base_conditions(source=source, exclude_product_ids=exclude, min_price=min_price)
  max_candidates = max(50, min(1500, int(max_candidates)))
  result = CandidateRetrievalResult(products=[])
  gender_cond = _fit_gender_condition(fit)
  if gender_cond is not None:
    base.append(gender_cond)

  if filters and filters.active:
    # Select from the catalog, not the current page. Explicit filters never relax.
    query = _ordered_recent(select(Product).where(*base, *filters.sql_conditions()))
    for product in db.execute(query.execution_options(yield_per=300)).scalars():
      if not product_gender_compatible(product, fit.gender_target if fit else None):
        continue
      if not filters.matches(product):
        continue
      _add_rows(result, [product], source_name="filters", max_candidates=max_candidates)
      if len(result.products) >= max_candidates:
        break
    return result

  feature_values = _merge_feature_values(
    _positive_feature_values(taste_features),
    _legacy_positive_values(taste),
  )

  taste_conds = []
  categories = {normalize_category(v) for v in feature_values.get("category", set()) if normalize_category(v)}
  brands = _norm_values(feature_values.get("brand", set()))
  colors = _norm_values(feature_values.get("color", set()))
  styles = _norm_values(feature_values.get("style", set()))
  silhouettes = _norm_values(feature_values.get("silhouette", set()))
  fits = _norm_values(feature_values.get("fit", set()))
  materials = _norm_values(feature_values.get("material", set()))
  occasions = _norm_values(feature_values.get("occasion", set()))
  if categories:
    taste_conds.append(Product.category.in_(list(categories)))
    taste_conds.append(Product.category_name.in_(list(categories)))
  if brands:
    taste_conds.append(func.lower(Product.brand).in_(list(brands)))
  if colors:
    taste_conds.append(func.lower(Product.color_family).in_(list(colors)))
  style_cond = _json_list_contains_any(Product.style_tags, styles)
  if style_cond is not None:
    taste_conds.append(style_cond)
  if silhouettes:
    taste_conds.append(func.lower(Product.silhouette).in_(list(silhouettes)))
  if fits:
    taste_conds.append(func.lower(Product.fit).in_(list(fits)))
  if materials:
    taste_conds.append(func.lower(Product.material).in_(list(materials)))
  if occasions:
    taste_conds.append(func.lower(Product.occasion).in_(list(occasions)))
  price_cond = _price_band_condition(feature_values.get("price_band", set()))
  if price_cond is not None:
    taste_conds.append(price_cond)
  if taste_conds:
    rows = db.execute(
      _ordered_recent(select(Product).where(and_(*base), or_(*taste_conds))).limit(300)
    ).scalars().all()
    _add_rows(result, rows, source_name="taste", max_candidates=max_candidates)

  fit_conds = list(base)
  gender_cond = _fit_gender_condition(fit)
  if gender_cond is not None:
    fit_conds.append(gender_cond)
  budget_cond = _fit_budget_condition(fit)
  if budget_cond is not None:
    fit_conds.append(budget_cond)
  size_cond = _fit_size_condition(fit)
  if size_cond is not None:
    fit_conds.append(size_cond)
  interest_categories = {
    normalize_category(str(x).strip())
    for x in ((fit.interest_categories or []) if fit else [])
    if str(x).strip()
  }
  interest_categories.discard("")
  if interest_categories:
    fit_conds.append(
      or_(
        Product.category.in_(list(interest_categories)),
        Product.category_name.in_(list(interest_categories)),
      )
    )
  rows = db.execute(_ordered_recent(select(Product).where(and_(*fit_conds))).limit(250)).scalars().all()
  _add_rows(result, rows, source_name="fit", max_candidates=max_candidates)

  scenario_norm = (scenario or "daily").strip().lower() or "daily"
  scenario_categories = SCENARIO_CATEGORIES.get(scenario_norm, set())
  scenario_conds = []
  if scenario_categories:
    scenario_conds.append(Product.category.in_(list(scenario_categories)))
    scenario_conds.append(Product.category_name.in_(list(scenario_categories)))
  scenario_conds.append(func.lower(Product.occasion) == scenario_norm)
  rows = db.execute(
    _ordered_recent(select(Product).where(and_(*base), or_(*scenario_conds))).limit(200)
  ).scalars().all()
  _add_rows(result, rows, source_name="scenario", max_candidates=max_candidates)

  rows = retrieve_embedding_candidates(
    db,
    user=user,
    limit=180,
    exclude_product_ids=exclude,
    source=source,
    min_price=min_price,
  )
  _add_rows(result, rows, source_name="embedding", max_candidates=max_candidates)

  since = datetime.now(timezone.utc) - timedelta(days=60)
  popular_ids = [
    str(pid)
    for pid in db.execute(
      select(RecommendationEventV2.product_id)
      .where(
        RecommendationEventV2.product_id.is_not(None),
        RecommendationEventV2.created_at >= since,
        RecommendationEventV2.event_type.in_(
          ["like", "save", "affiliate_click", "purchase", "post_purchase_positive", "buy_click"]
        ),
      )
      .group_by(RecommendationEventV2.product_id)
      .order_by(desc(func.count()))
      .limit(120)
    ).scalars()
    if str(pid).strip()
  ]
  if popular_ids:
    rows = db.execute(
      select(Product).where(and_(*base), Product.id.in_(popular_ids)).limit(120)
    ).scalars().all()
    _add_rows(result, rows, source_name="popular", max_candidates=max_candidates)

  top_categories = set(list(categories)[:6])
  exploration_conds = list(base)
  if top_categories:
    exploration_conds.append(Product.category.notin_(list(top_categories)))
  rows = db.execute(
    # TODO(MIE): replace random() with bucket/hash sampling before catalog scale-up.
    select(Product).where(and_(*exploration_conds)).order_by(func.random()).limit(120)
  ).scalars().all()
  _add_rows(result, rows, source_name="exploration", max_candidates=max_candidates)

  rows = db.execute(_ordered_recent(select(Product).where(and_(*base))).limit(250)).scalars().all()
  _add_rows(result, rows, source_name="fallback", max_candidates=max_candidates)

  return result
