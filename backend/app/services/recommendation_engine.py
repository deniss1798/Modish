from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import FitProfile, Product, StyleProfile, TasteProfile, User
from ..schemas.photo_analysis import extract_analysis_section


@dataclass(frozen=True)
class ScoredProduct:
  product: Product
  final_score: float
  breakdown: dict[str, float]
  reason: str


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

  fit_points = 0.0
  # colors
  if palette and (palette & p_colors):
    fit_points += 20.0
  if avoid_colors and (avoid_colors & p_colors):
    fit_points -= 30.0
  # silhouette
  if rec_sil and p_sil and p_sil in rec_sil:
    fit_points += 20.0
  if bad_sil and p_sil and p_sil in bad_sil:
    fit_points -= 30.0
  # size availability
  if product.available_sizes:
    if fit is None:
      fit_points += 8.0
    else:
      size = (fit.clothing_size or "").strip().upper()
      sizes = {str(x).strip().upper() for x in (product.available_sizes or []) if str(x).strip()}
      fit_points += 15.0 if (not size or size in sizes) else 0.0
  fit_score = max(0.0, min(100.0, 50.0 + fit_points)) / 100.0

  taste_points = 0.0
  if product.category and product.category.lower() in set(_norm_list(taste.liked_categories)):
    taste_points += 15.0
  if product.category and product.category.lower() in set(_norm_list(taste.disliked_categories)):
    taste_points -= 20.0
  if product.brand and product.brand.lower() in set(_norm_list(taste.liked_brands)):
    taste_points += 15.0
  if product.brand and product.brand.lower() in set(_norm_list(taste.disliked_brands)):
    taste_points -= 20.0
  if p_colors and (set(_norm_list(taste.liked_colors)) & p_colors):
    taste_points += 10.0
  if p_colors and (set(_norm_list(taste.disliked_colors)) & p_colors):
    taste_points -= 15.0
  taste_score = max(0.0, min(100.0, 50.0 + taste_points)) / 100.0

  price_score = 1.0
  # budget / price range (taste + fit budget max)
  budget_max = taste.price_max
  if fit is not None and int(fit.budget_max or 0) > 0:
    budget_max = min(budget_max, int(fit.budget_max))
  if product.price > budget_max:
    price_score = 0.4
  elif product.price < taste.price_min:
    price_score = 0.7

  availability_score = 1.0 if (product.image_url and product.product_url and product.available_sizes) else 0.4
  freshness_score = 1.0  # placeholder: can decay by age later

  final = (
    fit_score * 0.45
    + taste_score * 0.35
    + price_score * 0.10
    + freshness_score * 0.05
    + availability_score * 0.05
  )

  reason = "Подходит по палитре/силуэту и вашим предпочтениям."
  breakdown = {
    "fit_score": fit_score,
    "taste_score": taste_score,
    "price_score": price_score,
    "freshness_score": freshness_score,
    "availability_score": availability_score,
  }
  return ScoredProduct(product=product, final_score=float(final), breakdown=breakdown, reason=reason)


def generate_feed(db: Session, user: User, *, limit: int = 30) -> list[ScoredProduct]:
  products = db.execute(select(Product).where(Product.is_active == 1).limit(500)).scalars().all()
  scored = [score_product(db, user, p) for p in products]
  scored.sort(key=lambda x: x.final_score, reverse=True)
  return scored[: max(1, min(100, int(limit)))]

