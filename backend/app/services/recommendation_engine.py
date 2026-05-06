from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from ..catalog_normalize import normalize_category
from ..models import FitProfile, Product, StyleProfile, TasteProfile, User
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


def score_product(db: Session, user: User, product: Product) -> ScoredProduct:
  """
  Implements v1 scoring from spec:
  final_score = fit*0.45 + taste*0.35 + price*0.10 + freshness*0.05 + availability*0.05
  """
  profile = db.execute(select(StyleProfile).where(StyleProfile.user_id == user.id)).scalar_one()
  analysis = extract_analysis_section(profile.profile_json or {})
  taste = ensure_taste_profile(db, user.id)
  fit = db.execute(select(FitProfile).where(FitProfile.user_id == user.id)).scalar_one_or_none()

  palette = set(_norm_list(analysis.get("color_palette")))
  avoid_colors = set(_norm_list(analysis.get("avoid_colors")))
  rec_sil = set(_norm_list(analysis.get("recommended_silhouettes")))
  bad_sil = set(_norm_list(analysis.get("avoid_silhouettes")))

  p_colors = set(_norm_list(product.colors))
  p_sil = (product.silhouette or "").strip().lower()
  reasons: list[str] = []

  fit_points = 0.0
  # colors
  if palette and (palette & p_colors):
    fit_points += 20.0
    reasons.append("Цвет входит в вашу палитру")
  if avoid_colors and (avoid_colors & p_colors):
    fit_points -= 30.0
    reasons.append("Цвет совпадает с тем, что лучше избегать")
  # silhouette
  if rec_sil and p_sil and p_sil in rec_sil:
    fit_points += 20.0
    reasons.append("Силуэт подходит вашему профилю")
  if bad_sil and p_sil and p_sil in bad_sil:
    fit_points -= 30.0
    reasons.append("Силуэт в списке нежелательных")
  # size availability
  if product.available_sizes:
    if fit is None:
      fit_points += 8.0
    else:
      size = (fit.clothing_size or "").strip().upper()
      sizes = {str(x).strip().upper() for x in (product.available_sizes or []) if str(x).strip()}
      fit_points += 15.0 if (not size or size in sizes) else 0.0
      if size and size in sizes:
        reasons.append("Есть ваш размер")
  fit_score = max(0.0, min(100.0, 50.0 + fit_points)) / 100.0

  # Taste score from weights (fall back to legacy lists if empty)
  def w(d: Any, key: str) -> int:
    if not isinstance(d, dict):
      return 0
    v = d.get(key)
    try:
      return int(v)
    except Exception:
      return 0

  cat = (product.category or "").strip().lower()
  brand = (product.brand or "").strip().lower()
  style_tags = set(_norm_list(product.style_tags))

  taste_points = 0.0
  if taste.category_weights:
    taste_points += float(w(taste.category_weights, cat))
    if w(taste.category_weights, cat) >= 6:
      reasons.append("Вы часто выбираете эту категорию")
  else:
    if cat and cat in set(_norm_list(taste.liked_categories)):
      taste_points += 15.0
    if cat and cat in set(_norm_list(taste.disliked_categories)):
      taste_points -= 20.0

  if taste.brand_weights:
    taste_points += float(w(taste.brand_weights, brand))
    if w(taste.brand_weights, brand) >= 6:
      reasons.append("Вам часто нравится этот бренд")
  else:
    if brand and brand in set(_norm_list(taste.liked_brands)):
      taste_points += 15.0
    if brand and brand in set(_norm_list(taste.disliked_brands)):
      taste_points -= 20.0

  if taste.color_weights:
    for c in p_colors:
      taste_points += float(w(taste.color_weights, c))
      if w(taste.color_weights, c) >= 6:
        reasons.append("Вы часто выбираете похожие цвета")
  else:
    if p_colors and (set(_norm_list(taste.liked_colors)) & p_colors):
      taste_points += 10.0
    if p_colors and (set(_norm_list(taste.disliked_colors)) & p_colors):
      taste_points -= 15.0

  if taste.style_weights:
    for t in style_tags:
      taste_points += float(w(taste.style_weights, t))
      if w(taste.style_weights, t) >= 6:
        reasons.append("Вы часто выбираете похожие стили")
  else:
    if style_tags and (set(_norm_list(taste.liked_styles)) & style_tags):
      taste_points += 8.0
    if style_tags and (set(_norm_list(taste.disliked_styles)) & style_tags):
      taste_points -= 10.0

  # squash into 0..1
  taste_score = max(0.0, min(1.0, 0.5 + (taste_points / 60.0)))

  price_score = 1.0
  # budget / price range (taste + fit budget max)
  budget_max = taste.price_max
  if fit is not None and int(fit.budget_max or 0) > 0:
    budget_max = min(budget_max, int(fit.budget_max))
  if product.price > budget_max:
    price_score = 0.4
    reasons.append("Цена выше вашего бюджета")
  elif product.price < taste.price_min:
    price_score = 0.7
    reasons.append("Цена ниже вашего типичного диапазона")
  else:
    reasons.append("Цена в рамках бюджета")

  has_shop_url = bool(
    str(product.affiliate_url or "").strip() or str(product.product_url or "").strip()
  )
  availability_score = (
    1.0 if (product.image_url and has_shop_url and product.available_sizes) else 0.4
  )
  freshness_score = 1.0  # placeholder: can decay by age later

  final = (
    fit_score * 0.45
    + taste_score * 0.35
    + price_score * 0.10
    + freshness_score * 0.05
    + availability_score * 0.05
  )

  uniq: list[str] = []
  seen: set[str] = set()
  for r in reasons:
    if r not in seen:
      seen.add(r)
      uniq.append(r)
  reason = "Подходит по профилю и вашим предпочтениям."
  breakdown = {
    "fit_score": fit_score,
    "taste_score": taste_score,
    "price_score": price_score,
    "freshness_score": freshness_score,
    "availability_score": availability_score,
  }
  return ScoredProduct(
    product=product,
    final_score=float(final),
    breakdown=breakdown,
    reason=reason,
    reasons=uniq[:8],
  )


def generate_feed(
  db: Session,
  user: User,
  *,
  limit: int = 30,
  exclude_product_ids: set[str] | None = None,
) -> list[ScoredProduct]:
  exclude_product_ids = exclude_product_ids or set()
  min_price = 500
  q = (
    select(Product)
    .where(
      and_(
        Product.is_active == 1,
        Product.is_available == 1,
        Product.is_deleted_from_feed == 0,
        Product.price > min_price,
        Product.image_url.isnot(None),
        Product.image_url != "",
        or_(
          and_(Product.affiliate_url.isnot(None), Product.affiliate_url != ""),
          Product.product_url != "",
        ),
      )
    )
    .limit(1000)
  )
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
      pc = normalize_category(p.category or "")
      if pc not in interest_norm:
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

