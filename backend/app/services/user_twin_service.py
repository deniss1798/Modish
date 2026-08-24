"""User Twin v2: accumulated taste features with confidence and decay."""
from __future__ import annotations

from datetime import datetime, timezone
from math import exp
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..catalog_normalize import normalize_category, normalize_product_colors
from ..models import Product, TasteProfile, UserTasteFeature
from .recommendation_config import (
  GENERIC_DISLIKE_FEATURE_MULTIPLIER,
  USER_TWIN_CONFIDENCE_EVIDENCE_SCALE,
  USER_TWIN_STRONG_HALF_LIFE_DAYS,
  USER_TWIN_WEAK_HALF_LIFE_DAYS,
  dislike_reason_from_meta,
  normalize_event_type,
  user_twin_event_signal,
)


TASTE_FEATURE_TYPES = frozenset(
  {
    "category",
    "brand",
    "color",
    "style",
    "silhouette",
    "fit",
    "material",
    "occasion",
    "price_band",
    "item",
  }
)


def _now() -> datetime:
  return datetime.now(timezone.utc)


def _clamp(value: float, low: float, high: float) -> float:
  return max(low, min(high, float(value)))


def _norm(value: object) -> str:
  return str(value or "").strip().lower()


def _price_band(price: int | float | None) -> str:
  amount = float(price or 0)
  if amount <= 0:
    return ""
  if amount < 2_000:
    return "budget"
  if amount < 6_000:
    return "mid"
  if amount < 15_000:
    return "premium"
  return "luxury"


def _category_values(product: Product) -> set[str]:
  out: set[str] = set()
  for raw in (product.category_name, product.category):
    value = normalize_category(str(raw or "").strip())
    if value:
      out.add(value)
  return out


def product_taste_features(product: Product) -> dict[str, set[str]]:
  colors = set(normalize_product_colors(list(product.colors or [])))
  colors |= {_norm(c) for c in (product.colors or []) if _norm(c)}
  if getattr(product, "color_family", None):
    colors.add(_norm(product.color_family))
  if getattr(product, "color_original", None):
    colors |= set(normalize_product_colors([product.color_original]))
    colors.add(_norm(product.color_original))

  features: dict[str, set[str]] = {
    "item": {str(product.id)} if product.id else set(),
    "category": _category_values(product),
    "brand": {_norm(product.brand)} if _norm(product.brand) else set(),
    "color": {c for c in colors if c},
    "style": {_norm(t) for t in (product.style_tags or []) if _norm(t)},
    "silhouette": {_norm(product.silhouette)} if _norm(product.silhouette) else set(),
    "fit": {_norm(product.fit)} if _norm(product.fit) else set(),
    "material": {_norm(product.material)} if _norm(product.material) else set(),
    "occasion": {_norm(product.occasion)} if _norm(product.occasion) else set(),
    "price_band": {_price_band(product.price)} if _price_band(product.price) else set(),
  }
  return {k: v for k, v in features.items() if v}


def _targets_for_event(event_type: str, meta: dict | None) -> set[str]:
  event_type = normalize_event_type(event_type)
  if event_type == "impression":
    return set()
  if event_type == "view":
    return {"category", "style"}
  if event_type == "skip":
    return {"category", "style"}
  if event_type in {
    "like",
    "save",
    "open_product",
    "affiliate_click",
    "purchase",
    "post_purchase_positive",
  }:
    return {
      "category",
      "brand",
      "color",
      "style",
      "silhouette",
      "fit",
      "material",
      "occasion",
      "price_band",
    }
  if event_type == "unsave":
    return {"category", "brand", "color", "style"}
  if event_type in {"dislike", "post_purchase_negative"}:
    reason = dislike_reason_from_meta(meta)
    if reason == "dont_like_color":
      return {"item", "color"}
    if reason == "dont_like_category":
      return {"item", "category"}
    if reason == "dont_like_brand":
      return {"item", "brand"}
    if reason in {"not_my_style", "dont_like_design"}:
      return {"item", "style", "category"}
    if reason == "dont_like_fit":
      return {"item", "fit", "silhouette"}
    if reason == "too_expensive":
      return {"item", "price_band"}
    if reason in {"already_have_similar", "other"}:
      return {"item"}
    return {"item", "category", "style"}
  return set()


def _target_multiplier(event_type: str, meta: dict | None, feature_type: str) -> float:
  if event_type in {"dislike", "post_purchase_negative"}:
    reason = dislike_reason_from_meta(meta)
    if reason is None and feature_type != "item":
      return GENERIC_DISLIKE_FEATURE_MULTIPLIER
  return 1.0


def _ensure_taste_profile(db: Session, user_id: str, *, as_of: datetime) -> TasteProfile:
  row = db.execute(select(TasteProfile).where(TasteProfile.user_id == user_id)).scalar_one_or_none()
  if row:
    return row
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
    profile_version=1,
    created_at=as_of,
    updated_at=as_of,
  )
  db.add(row)
  db.flush()
  return row


def _get_or_create_feature(
  db: Session,
  *,
  user_id: str,
  feature_type: str,
  feature_value: str,
  as_of: datetime,
) -> UserTasteFeature:
  row = db.execute(
    select(UserTasteFeature).where(
      UserTasteFeature.user_id == user_id,
      UserTasteFeature.feature_type == feature_type,
      UserTasteFeature.feature_value == feature_value,
    )
  ).scalar_one_or_none()
  if row:
    return row
  row = UserTasteFeature(
    id=str(uuid4()),
    user_id=user_id,
    feature_type=feature_type,
    feature_value=feature_value,
    preference_score=0.0,
    confidence=0.0,
    positive_count=0,
    negative_count=0,
    created_at=as_of,
    updated_at=as_of,
  )
  db.add(row)
  db.flush()
  return row


def update_confidence(feature: UserTasteFeature) -> float:
  positive = int(feature.positive_count or 0)
  negative = int(feature.negative_count or 0)
  total = positive + negative
  if total <= 0:
    feature.confidence = 0.0
    return 0.0
  consistency = abs(positive - negative) / total
  evidence = 1.0 - exp(-total / USER_TWIN_CONFIDENCE_EVIDENCE_SCALE)
  confidence = evidence * (0.55 + 0.45 * consistency)
  feature.confidence = _clamp(confidence, 0.0, 1.0)
  return float(feature.confidence)


def _decay_multiplier(*, days: float, half_life_days: float) -> float:
  if days <= 0:
    return 1.0
  return 0.5 ** (days / max(1.0, half_life_days))


def apply_decay(
  feature: UserTasteFeature,
  *,
  as_of: datetime | None = None,
  half_life_days: float | None = None,
) -> UserTasteFeature:
  as_of = as_of or _now()
  last = feature.updated_at or feature.created_at
  if last is None:
    return feature
  if last.tzinfo is None:
    last = last.replace(tzinfo=timezone.utc)
  total = int(feature.positive_count or 0) + int(feature.negative_count or 0)
  default_half_life = (
    USER_TWIN_STRONG_HALF_LIFE_DAYS
    if total >= 8 or float(feature.confidence or 0.0) >= 0.75
    else USER_TWIN_WEAK_HALF_LIFE_DAYS
  )
  half_life = float(half_life_days or default_half_life)
  days = max(0.0, (as_of - last).total_seconds() / 86_400)
  score_decay = _decay_multiplier(days=days, half_life_days=half_life)
  confidence_decay = _decay_multiplier(days=days, half_life_days=half_life * 2)
  feature.preference_score = _clamp(float(feature.preference_score or 0.0) * score_decay, -1.0, 1.0)
  feature.confidence = _clamp(float(feature.confidence or 0.0) * confidence_decay, 0.0, 1.0)
  feature.updated_at = as_of
  return feature


def _apply_signal(feature: UserTasteFeature, *, signal: float, as_of: datetime) -> None:
  if signal == 0:
    return
  apply_decay(feature, as_of=as_of)
  feature.preference_score = _clamp(float(feature.preference_score or 0.0) + signal, -1.0, 1.0)
  if signal > 0:
    feature.positive_count = int(feature.positive_count or 0) + 1
    feature.last_positive_at = as_of
  elif signal < 0:
    feature.negative_count = int(feature.negative_count or 0) + 1
    feature.last_negative_at = as_of
  update_confidence(feature)
  feature.updated_at = as_of


def apply_event_to_user_twin(
  db: Session,
  *,
  user_id: str,
  product: Product,
  event_type: str,
  meta: dict | None = None,
  occurred_at: datetime | None = None,
) -> list[UserTasteFeature]:
  occurred_at = occurred_at or _now()
  event_type = normalize_event_type(event_type)
  signal = user_twin_event_signal(event_type)
  targets = _targets_for_event(event_type, meta)
  if not targets or signal == 0:
    return []

  features = product_taste_features(product)
  touched: list[UserTasteFeature] = []
  for feature_type in sorted(targets):
    if feature_type not in TASTE_FEATURE_TYPES:
      continue
    multiplier = _target_multiplier(event_type, meta, feature_type)
    feature_signal = signal * multiplier
    for feature_value in sorted(features.get(feature_type, set())):
      row = _get_or_create_feature(
        db,
        user_id=user_id,
        feature_type=feature_type,
        feature_value=feature_value,
        as_of=occurred_at,
      )
      _apply_signal(row, signal=feature_signal, as_of=occurred_at)
      touched.append(row)

  if touched:
    taste = _ensure_taste_profile(db, user_id, as_of=occurred_at)
    taste.profile_version = int(taste.profile_version or 1) + 1
    taste.updated_at = occurred_at
  db.flush()
  return touched


def get_user_taste_features(
  db: Session,
  *,
  user_id: str,
  feature_type: str | None = None,
  min_confidence: float = 0.0,
) -> list[UserTasteFeature]:
  conds = [UserTasteFeature.user_id == user_id]
  if feature_type:
    conds.append(UserTasteFeature.feature_type == feature_type)
  rows = db.execute(select(UserTasteFeature).where(*conds)).scalars().all()
  rows = [r for r in rows if float(r.confidence or 0.0) >= min_confidence]
  rows.sort(key=lambda r: (float(r.confidence or 0.0), abs(float(r.preference_score or 0.0))), reverse=True)
  return rows


def get_feature_preference(
  db: Session,
  *,
  user_id: str,
  feature_type: str,
  feature_value: str,
  apply_time_decay: bool = False,
  as_of: datetime | None = None,
) -> UserTasteFeature | None:
  row = db.execute(
    select(UserTasteFeature).where(
      UserTasteFeature.user_id == user_id,
      UserTasteFeature.feature_type == feature_type,
      UserTasteFeature.feature_value == _norm(feature_value),
    )
  ).scalar_one_or_none()
  if row and apply_time_decay:
    apply_decay(row, as_of=as_of)
    db.flush()
  return row
