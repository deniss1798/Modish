"""Candidate Retrieval v2 for MIE.

Builds a deduplicated candidate pool from multiple full-catalog-capable sources
before ranking.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.orm import Session

from ..catalog_normalize import normalize_category
from ..models import FitProfile, Product, RecommendationEventV2, TasteProfile, User
from .mie_scoring import TasteFeatureMap


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
  return or_(
    Product.gender_target == gender,
    Product.gender_target == "unisex",
    Product.gender_target.is_(None),
    Product.gender_target == "",
  )


def _add_rows(
  result: CandidateRetrievalResult,
  rows: Iterable[Product],
  *,
  source_name: str,
  max_candidates: int,
) -> None:
  seen = {p.id for p in result.products}
  for product in rows:
    if not product or not product.id:
      continue
    result.sources_by_product_id.setdefault(product.id, set()).add(source_name)
    if product.id in seen:
      continue
    result.products.append(product)
    seen.add(product.id)
    if len(result.products) >= max_candidates:
      return


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
) -> CandidateRetrievalResult:
  del user  # reserved for future per-user source policies
  exclude = {str(pid) for pid in (exclude_product_ids or set()) if str(pid).strip()}
  base = _base_conditions(source=source, exclude_product_ids=exclude, min_price=min_price)
  max_candidates = max(50, min(1500, int(max_candidates)))
  result = CandidateRetrievalResult(products=[])

  feature_values = _merge_feature_values(
    _positive_feature_values(taste_features),
    _legacy_positive_values(taste),
  )

  taste_conds = []
  categories = {normalize_category(v) for v in feature_values.get("category", set()) if normalize_category(v)}
  brands = {v for v in feature_values.get("brand", set()) if v}
  colors = {v for v in feature_values.get("color", set()) if v}
  if categories:
    taste_conds.append(Product.category.in_(list(categories)))
    taste_conds.append(Product.category_name.in_(list(categories)))
  if brands:
    taste_conds.append(func.lower(Product.brand).in_(list(brands)))
  if colors:
    taste_conds.append(func.lower(Product.color_family).in_(list(colors)))
  if taste_conds:
    rows = db.execute(
      _ordered_recent(select(Product).where(and_(*base), or_(*taste_conds))).limit(300)
    ).scalars().all()
    _add_rows(result, rows, source_name="taste", max_candidates=max_candidates)

  fit_conds = list(base)
  gender_cond = _fit_gender_condition(fit)
  if gender_cond is not None:
    fit_conds.append(gender_cond)
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
    select(Product).where(and_(*exploration_conds)).order_by(func.random()).limit(120)
  ).scalars().all()
  _add_rows(result, rows, source_name="exploration", max_candidates=max_candidates)

  rows = db.execute(_ordered_recent(select(Product).where(and_(*base))).limit(250)).scalars().all()
  _add_rows(result, rows, source_name="fallback", max_candidates=max_candidates)

  return result
