"""Rule-based product scoring for personalized feed (v2)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from ..catalog_normalize import normalize_category
from ..models import FitProfile, Product, RecommendationEventV2, StyleProfile, TasteProfile, User
from .catalog.rule_filters import load_rules_by_source_id, product_gender_compatible, product_passes_source_rules
from ..schemas.photo_analysis import extract_analysis_section


@dataclass(frozen=True)
class ScoredProduct:
  product: Product
  final_score: float
  breakdown: dict[str, float]
  reason: str
  reasons: list[str]


def _norm_list(values: Any) -> list[str]:
  if not isinstance(values, list):
    return []
  out: list[str] = []
  for x in values:
    s = str(x).strip().lower()
    if s and s not in out:
      out.append(s)
  return out


def ensure_taste_profile(db: Session, user_id: str) -> TasteProfile:
  row = db.execute(select(TasteProfile).where(TasteProfile.user_id == user_id)).scalar_one_or_none()
  if row:
    return row
  now = datetime.now(timezone.utc)
  row = TasteProfile(
    id=str(uuid4()),
    user_id=user_id,
    liked_categories=[],
    disliked_categories=[],
    liked_colors=[],
    disliked_colors=[],
    liked_brands=[],
    disliked_brands=[],
    liked_styles=[],
    disliked_styles=[],
    category_weights={},
    brand_weights={},
    color_weights={},
    style_weights={},
    price_min=0,
    price_max=10_000,
    preferred_fit="regular",
    created_at=now,
    updated_at=now,
  )
  db.add(row)
  db.flush()
  return row


def _product_category_norms(product: Product) -> set[str]:
  keys: set[str] = set()
  for raw in (product.category_name, product.category):
    if raw and str(raw).strip():
      k = normalize_category(str(raw).strip())
      if k:
        keys.add(k)
  return keys


def _weight(d: Any, key: str) -> int:
  if not isinstance(d, dict):
    return 0
  try:
    return int(d.get(key, 0))
  except Exception:
    return 0


def _recent_product_engagement(db: Session, *, user_id: str, product_id: str) -> int:
  since = datetime.now(timezone.utc) - timedelta(days=30)
  n = db.execute(
    select(func.count())
    .select_from(RecommendationEventV2)
    .where(
      RecommendationEventV2.user_id == user_id,
      RecommendationEventV2.product_id == product_id,
      RecommendationEventV2.event_type.in_(["view", "open_product"]),
      RecommendationEventV2.created_at >= since,
    )
  ).scalar_one()
  return int(n or 0)


def _user_saved_category_boost(db: Session, *, user_id: str, cat_keys: set[str]) -> float:
  if not cat_keys:
    return 0.0
  since = datetime.now(timezone.utc) - timedelta(days=90)
  rows = db.execute(
    select(Product.category, Product.category_name)
    .join(RecommendationEventV2, RecommendationEventV2.product_id == Product.id)
    .where(
      RecommendationEventV2.user_id == user_id,
      RecommendationEventV2.event_type == "save",
      RecommendationEventV2.created_at >= since,
    )
    .limit(200)
  ).all()
  saved_cats: set[str] = set()
  for cat, cat_name in rows:
    for raw in (cat_name, cat):
      if raw and str(raw).strip():
        k = normalize_category(str(raw).strip())
        if k:
          saved_cats.add(k)
  if saved_cats & cat_keys:
    return 8.0
  return 0.0


def score_product(db: Session, user: User, product: Product) -> ScoredProduct:
  """
  Rule-based score (v2):
  category_match + size_match + color_match + budget_match + style_match
  + liked_brand_boost + saved_category_boost
  - disliked_color_penalty - disliked_category_penalty - already_seen_penalty
  """
  profile = db.execute(select(StyleProfile).where(StyleProfile.user_id == user.id)).scalar_one()
  analysis = extract_analysis_section(profile.profile_json or {})
  taste = ensure_taste_profile(db, user.id)
  fit = db.execute(select(FitProfile).where(FitProfile.user_id == user.id)).scalar_one_or_none()

  palette = set(_norm_list(analysis.get("color_palette")))
  avoid_colors = set(_norm_list(analysis.get("avoid_colors")))
  cat_keys = _product_category_norms(product)
  p_colors = set(_norm_list(product.colors))
  brand = (product.brand or "").strip().lower()
  style_tags = set(_norm_list(product.style_tags))
  reasons: list[str] = []
  bd: dict[str, float] = {}

  score = 0.0

  # category_match
  cat_match = 0.0
  liked_norm = {normalize_category(str(x).strip()) for x in (taste.liked_categories or []) if str(x).strip()}
  disliked_norm = {normalize_category(str(x).strip()) for x in (taste.disliked_categories or []) if str(x).strip()}
  if taste.category_weights and cat_keys:
    cat_match = float(max((_weight(taste.category_weights, ck) for ck in cat_keys), default=0))
    if cat_match >= 6:
      reasons.append("Категория вам подходит")
  elif cat_keys & liked_norm:
    cat_match = 15.0
    reasons.append("Вы часто выбираете эту категорию")
  if fit and fit.interest_categories and cat_keys:
    interest = {normalize_category(str(x).strip()) for x in fit.interest_categories if str(x).strip()}
    if cat_keys & interest:
      cat_match = max(cat_match, 12.0)
      reasons.append("Совпадает с вашими интересами")
  score += cat_match
  bd["category_match"] = cat_match

  # disliked_category_penalty
  disliked_cat_pen = 0.0
  if cat_keys & disliked_norm:
    disliked_cat_pen = 20.0
    reasons.append("Категория вам не нравится")
  elif taste.category_weights and cat_keys:
    neg = min((_weight(taste.category_weights, ck) for ck in cat_keys), default=0)
    if neg < -2:
      disliked_cat_pen = min(20.0, float(-neg))
  score -= disliked_cat_pen
  bd["disliked_category_penalty"] = disliked_cat_pen

  # size_match
  size_match = 0.0
  if product.available_sizes:
    if fit and (fit.clothing_size or "").strip():
      size = fit.clothing_size.strip().upper()
      sizes = {str(x).strip().upper() for x in product.available_sizes if str(x).strip()}
      if size in sizes:
        size_match = 12.0
        reasons.append("Есть ваш размер")
    else:
      size_match = 6.0
  score += size_match
  bd["size_match"] = size_match

  # color_match
  color_match = 0.0
  if palette and (palette & p_colors):
    color_match += 12.0
    reasons.append("Цвет из вашей палитры")
  if taste.color_weights:
    for c in p_colors:
      w = _weight(taste.color_weights, c)
      if w > 0:
        color_match += min(8.0, float(w))
      if w >= 6:
        reasons.append("Похожий цвет вам нравится")
  elif p_colors and (set(_norm_list(taste.liked_colors)) & p_colors):
    color_match = max(color_match, 10.0)
  score += color_match
  bd["color_match"] = color_match

  # disliked_color_penalty
  disliked_color_pen = 0.0
  if avoid_colors and (avoid_colors & p_colors):
    disliked_color_pen += 12.0
    reasons.append("Цвет лучше избегать")
  if p_colors and (set(_norm_list(taste.disliked_colors)) & p_colors):
    disliked_color_pen = max(disliked_color_pen, 15.0)
  elif taste.color_weights:
    for c in p_colors:
      w = _weight(taste.color_weights, c)
      if w < -2:
        disliked_color_pen = max(disliked_color_pen, min(15.0, float(-w)))
  score -= disliked_color_pen
  bd["disliked_color_penalty"] = disliked_color_pen

  # budget_match
  lo = int(taste.price_min or 0)
  hi = int(taste.price_max or 200_000)
  if fit is not None:
    bmin = int(fit.budget_min or 0)
    bmax = int(fit.budget_max or 0)
    if bmin > 0:
      lo = max(lo, bmin)
    if bmax > 0:
      hi = min(hi, bmax)
  if hi < lo:
    lo, hi = hi, lo
  if hi == lo:
    hi = lo + 1
  pr = int(product.price or 0)
  budget_match = 0.0
  if lo <= pr <= hi:
    budget_match = 10.0
    reasons.append("Цена в рамках бюджета")
  elif pr <= hi * 1.15:
    budget_match = 4.0
  else:
    budget_match = -6.0
    reasons.append("Выше бюджета")
  score += budget_match
  bd["budget_match"] = budget_match
  bd["budget_lo"] = float(lo)
  bd["budget_hi"] = float(hi)

  # style_match
  style_match = 0.0
  fit_styles = set(_norm_list(fit.style_scenarios if fit else []))
  if fit_styles and style_tags and (fit_styles & style_tags):
    style_match = 8.0
    reasons.append("Подходит под ваш сценарий")
  elif taste.style_weights:
    for t in style_tags:
      w = _weight(taste.style_weights, t)
      if w > 0:
        style_match += min(6.0, float(w))
  elif style_tags and (set(_norm_list(taste.liked_styles)) & style_tags):
    style_match = max(style_match, 8.0)
  score += style_match
  bd["style_match"] = style_match

  # liked_brand_boost
  brand_boost = 0.0
  if taste.brand_weights and brand:
    brand_boost = float(_weight(taste.brand_weights, brand))
    if brand_boost >= 6:
      reasons.append("Бренд вам нравится")
  elif brand and brand in set(_norm_list(taste.liked_brands)):
    brand_boost = 10.0
    reasons.append("Любимый бренд")
  if brand and brand in set(_norm_list(taste.disliked_brands)):
    brand_boost -= 12.0
  score += brand_boost
  bd["liked_brand_boost"] = brand_boost

  # saved_category_boost
  saved_boost = _user_saved_category_boost(db, user_id=user.id, cat_keys=cat_keys)
  if saved_boost > 0:
    reasons.append("Похоже на сохранённые вещи")
  score += saved_boost
  bd["saved_category_boost"] = saved_boost

  # already_seen_penalty
  eng_n = _recent_product_engagement(db, user_id=user.id, product_id=product.id)
  seen_pen = min(15.0, float(eng_n) * 5.0)
  if seen_pen > 0:
    reasons.append("Вы уже смотрели этот товар")
  score -= seen_pen
  bd["already_seen_penalty"] = seen_pen
  bd["engagement_recent_views"] = float(eng_n)

  final = max(0.0, score)
  bd["raw_score"] = final

  uniq: list[str] = []
  seen: set[str] = set()
  for r in reasons:
    if r not in seen:
      seen.add(r)
      uniq.append(r)
  reason = uniq[0] if uniq else "Подобрано под ваш профиль"
  return ScoredProduct(
    product=product,
    final_score=final,
    breakdown=bd,
    reason=reason,
    reasons=uniq[:8],
  )


def generate_feed(
  db: Session,
  user: User,
  *,
  limit: int = 30,
  exclude_product_ids: set[str] | None = None,
  source: str | None = None,
) -> list[ScoredProduct]:
  exclude_product_ids = exclude_product_ids or set()
  min_price = 500
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
  q = select(Product).where(and_(*conds)).limit(1000)
  products = db.execute(q).scalars().all()
  if exclude_product_ids:
    products = [p for p in products if p.id not in exclude_product_ids]
  rules_by_source = load_rules_by_source_id(db)
  fit = db.execute(select(FitProfile).where(FitProfile.user_id == user.id)).scalar_one_or_none()
  user_gender = fit.gender_target if fit else None
  interest_norm: set[str] = set()
  if fit and fit.interest_categories:
    interest_norm = {
      normalize_category(str(x).strip()) for x in fit.interest_categories if str(x).strip()
    }
  filtered: list[Product] = []
  for p in products:
    if not product_passes_source_rules(p, rules_by_source):
      continue
    if not product_gender_compatible(p, user_gender):
      continue
    if interest_norm:
      p_cats = _product_category_norms(p)
      if p_cats and not (p_cats & interest_norm):
        continue
    filtered.append(p)
  scored = [score_product(db, user, p) for p in filtered]
  scored.sort(key=lambda x: x.final_score, reverse=True)
  lim = max(1, min(100, int(limit)))
  top = scored[:lim]
  from .recommendation_cache_service import persist_recommendation_caches  # noqa: PLC0415

  persist_recommendation_caches(
    db,
    user_id=user.id,
    filtered_products=filtered,
    scored_top=top,
  )
  return top
