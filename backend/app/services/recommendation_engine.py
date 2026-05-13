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
  """Ключи категории для скоринга и фильтра интересов (витрина + фид)."""
  keys: set[str] = set()
  for raw in (product.category_name, product.category):
    if raw and str(raw).strip():
      k = normalize_category(str(raw).strip())
      if k:
        keys.add(k)
  return keys


def _recent_product_engagement(db: Session, *, user_id: str, product_id: str) -> int:
  """Сколько раз пользователь смотрел карточку (view/open) за последние 30 дней."""
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

  cat_keys = _product_category_norms(product)
  brand = (product.brand or "").strip().lower()
  style_tags = set(_norm_list(product.style_tags))

  taste_points = 0.0
  if taste.category_weights and cat_keys:
    best_w = max((w(taste.category_weights, ck) for ck in cat_keys), default=0)
    taste_points += float(best_w)
    if best_w >= 6:
      reasons.append("Вы часто выбираете эту категорию")
  elif cat_keys:
    liked_norm = {normalize_category(str(x).strip()) for x in (taste.liked_categories or []) if str(x).strip()}
    disliked_norm = {normalize_category(str(x).strip()) for x in (taste.disliked_categories or []) if str(x).strip()}
    if cat_keys & liked_norm:
      taste_points += 15.0
    if cat_keys & disliked_norm:
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

  eng_n = _recent_product_engagement(db, user_id=user.id, product_id=product.id)
  if eng_n > 0:
    taste_points += min(10.0, 2.0 + 3.0 * float(eng_n - 1))
    reasons.append("Вы уже смотрели этот товар")

  # squash into 0..1
  taste_score = max(0.0, min(1.0, 0.5 + (taste_points / 60.0)))

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
  if pr > hi:
    over = (pr - hi) / max(float(hi), 1.0)
    price_score = max(0.12, 0.52 - min(0.4, over * 0.1))
    reasons.append("Цена выше вашего бюджета")
  elif lo > 0 and pr < lo:
    price_score = 0.72
    reasons.append("Дешевле заданного диапазона")
  else:
    mid = (lo + hi) / 2.0
    span = max(float(hi - lo), 1.0)
    dist = abs(float(pr) - mid) / (span / 2.0)
    dist = min(1.0, dist)
    price_score = 0.82 + 0.18 * (1.0 - dist)
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
    "budget_lo": float(lo),
    "budget_hi": float(hi),
    "engagement_recent_views": float(eng_n),
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

