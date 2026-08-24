"""MIE v4 score separation: fit, taste, context, quality, exploration."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..catalog_normalize import (
  normalize_category,
  product_gender_from_model,
)
from ..models import FitProfile, Product, TasteProfile, UserTasteFeature
from .catalog.catalog_quality import product_is_feed_eligible
from .catalog.rule_filters import product_gender_compatible
from .feed_filters import product_passes_budget, product_passes_size
from .recommendation_config import FINAL_SCORE_WEIGHTS, FIT_MIN_THRESHOLD
from .user_twin_service import product_taste_features


TasteFeatureMap = dict[tuple[str, str], tuple[float, float]]


@dataclass(frozen=True)
class MIEScore:
  final_score: float
  fit_score: float
  taste_score: float
  context_score: float
  quality_score: float
  exploration_score: float
  hard_reject: bool
  reasons: list[str] = field(default_factory=list)
  debug: dict[str, float] = field(default_factory=dict)


def _clamp01(value: float) -> float:
  return max(0.0, min(1.0, float(value)))


def _norm_feature(value: object) -> str:
  return str(value or "").strip().lower()


def load_user_taste_feature_map(db: Session, *, user_id: str) -> TasteFeatureMap:
  rows = db.execute(
    select(UserTasteFeature).where(UserTasteFeature.user_id == user_id)
  ).scalars().all()
  out: TasteFeatureMap = {}
  for row in rows:
    key = (str(row.feature_type or "").strip(), str(row.feature_value or "").strip().lower())
    if not key[0] or not key[1]:
      continue
    out[key] = (float(row.preference_score or 0.0), float(row.confidence or 0.0))
  return out


def profile_confidence(taste_features: TasteFeatureMap) -> float:
  if not taste_features:
    return 0.0
  ranked = sorted((conf for _, conf in taste_features.values()), reverse=True)[:20]
  if not ranked:
    return 0.0
  return _clamp01(sum(ranked) / len(ranked))


def compute_fit_score(
  product: Product,
  *,
  fit: FitProfile | None,
  palette: set[str],
  avoid_colors: set[str],
) -> tuple[float, bool, list[str], dict[str, float]]:
  reasons: list[str] = []
  debug: dict[str, float] = {}
  if not product_is_feed_eligible(product):
    return 0.0, True, ["Недостаточно данных о товаре"], {"fit_catalog_eligible": 0.0}
  if not product.affiliate_url and not product.product_url:
    return 0.0, True, ["Нет рабочей ссылки на товар"], {"fit_link_ok": 0.0}

  score = 0.55
  hard_reject = False

  user_gender = fit.gender_target if fit else None
  pg = product_gender_from_model(product)
  if fit and (fit.gender_target or "").strip().lower() in {"menswear", "womenswear"}:
    if not product_gender_compatible(product, user_gender):
      hard_reject = True
      score = 0.0
      reasons.append("Не подходит по полу")
    elif pg == fit.gender_target:
      score += 0.18
      reasons.append("Под ваш профиль")
    elif pg == "unisex":
      score += 0.08
  else:
    score += 0.08
  debug["fit_gender"] = 0.0 if hard_reject else score

  if not hard_reject and fit is not None:
    if product_passes_size(product, fit):
      if product.available_sizes:
        score += 0.10
        reasons.append("Есть ваш размер")
      else:
        score += 0.04
    else:
      score = min(score, 0.35)
      hard_reject = True
      reasons.append("Нет подходящего размера")
    if product_passes_budget(product, fit):
      score += 0.08
      reasons.append("Цена в рамках бюджета")
    else:
      score = min(score, 0.35)
      hard_reject = True
      reasons.append("Выше бюджета")
  elif not hard_reject:
    score += 0.06

  colors = set(product_taste_features(product).get("color", set()))
  if not hard_reject and palette and colors & palette:
    score += 0.07
    reasons.append("Цвет из вашей палитры")
  if not hard_reject and avoid_colors and colors & avoid_colors:
    score -= 0.15
    reasons.append("Цвет лучше избегать")

  if not hard_reject and fit is not None:
    silhouettes = {_norm_feature(product.silhouette)} if product.silhouette else set()
    recommended = {_norm_feature(x) for x in (fit.recommended_silhouettes or []) if _norm_feature(x)}
    avoid = {_norm_feature(x) for x in (fit.avoid_silhouettes or []) if _norm_feature(x)}
    if silhouettes and recommended and silhouettes & recommended:
      score += 0.05
    if silhouettes and avoid and silhouettes & avoid:
      score -= 0.12

  fit_score = _clamp01(score)
  if fit_score < FIT_MIN_THRESHOLD:
    hard_reject = True
  debug["fit_score"] = fit_score
  return fit_score, hard_reject, reasons, debug


def _legacy_weight(d: Any, key: str) -> float:
  if not isinstance(d, dict):
    return 0.0
  try:
    return float(d.get(key, 0.0))
  except Exception:
    return 0.0


def compute_taste_score(
  product: Product,
  *,
  taste: TasteProfile,
  taste_features: TasteFeatureMap,
) -> tuple[float, list[str], dict[str, float]]:
  features = product_taste_features(product)
  type_weights = {
    "item": 1.2,
    "category": 1.0,
    "style": 1.0,
    "color": 0.8,
    "brand": 0.7,
    "silhouette": 0.5,
    "fit": 0.4,
    "material": 0.35,
    "occasion": 0.35,
    "price_band": 0.25,
  }
  known_weighted = 0.0
  known_weight = 0.0
  possible_weight = 0.0
  strongest_positive = 0.0
  strongest_negative = 0.0
  for feature_type, values in features.items():
    tw = type_weights.get(feature_type, 0.2)
    for value in values:
      possible_weight += tw
      key = (feature_type, str(value).lower())
      if key not in taste_features:
        continue
      pref, conf = taste_features[key]
      if abs(float(pref or 0.0)) <= 0 and float(conf or 0.0) <= 0:
        continue
      contribution = _clamp01(conf) * _clamp01(abs(pref)) * (1 if pref >= 0 else -1)
      known_weighted += tw * contribution
      known_weight += tw
      strongest_positive = max(strongest_positive, contribution)
      strongest_negative = min(strongest_negative, contribution)

  # Legacy TasteProfile weights stay as a low-confidence fallback until MIE v4 fully owns ranking.
  legacy_weighted = 0.0
  legacy_total = 0.0
  for category in features.get("category", set()):
    weight = _legacy_weight(taste.category_weights, category)
    if weight == 0:
      continue
    legacy_weighted += _clamp01(abs(weight) / 12.0) * (1 if weight >= 0 else -1)
    legacy_total += 1.0
  for style in features.get("style", set()):
    weight = _legacy_weight(taste.style_weights, style)
    if weight == 0:
      continue
    legacy_weighted += _clamp01(abs(weight) / 12.0) * (1 if weight >= 0 else -1)
    legacy_total += 1.0
  for color in features.get("color", set()):
    weight = _legacy_weight(taste.color_weights, color)
    if weight == 0:
      continue
    legacy_weighted += 0.8 * _clamp01(abs(weight) / 12.0) * (1 if weight >= 0 else -1)
    legacy_total += 0.8
  for brand in features.get("brand", set()):
    weight = _legacy_weight(taste.brand_weights, brand)
    if weight == 0:
      continue
    legacy_weighted += 0.7 * _clamp01(abs(weight) / 12.0) * (1 if weight >= 0 else -1)
    legacy_total += 0.7

  normalized = (known_weighted / max(known_weight, 1.0)) if known_weight > 0 else 0.0
  legacy_normalized = (legacy_weighted / legacy_total) if legacy_total > 0 else 0.0
  confidence = profile_confidence(taste_features)
  user_twin_share = 1.0 if known_weight > 0 and legacy_total <= 0 else 0.75 if known_weight > 0 else 0.0
  legacy_share = 1.0 - user_twin_share if legacy_total > 0 else 0.0
  combined = normalized * user_twin_share + legacy_normalized * legacy_share
  trust = 0.35 + 0.65 * confidence if known_weight > 0 else 0.35
  score = _clamp01(0.5 + combined * 0.5 * trust)

  reasons: list[str] = []
  if strongest_positive > 0.2 or legacy_normalized > 0.2:
    reasons.append("Похожий вкус вы уже выбирали")
  if strongest_negative < -0.2 or legacy_normalized < -0.2:
    reasons.append("Есть признаки, которые вам нравятся меньше")
  return score, reasons, {
    "taste_user_twin_signal": float(normalized),
    "taste_legacy_signal": float(legacy_normalized),
    "taste_known_weight": float(known_weight),
    "taste_possible_weight": float(possible_weight),
    "taste_profile_confidence": confidence,
  }


def compute_context_score(
  product: Product,
  *,
  fit: FitProfile | None,
  scenario: str = "daily",
) -> tuple[float, list[str], dict[str, float]]:
  score = 0.5
  reasons: list[str] = []
  features = product_taste_features(product)
  product_categories = {normalize_category(v) for v in features.get("category", set())}
  product_styles = set(features.get("style", set()))
  product_occasion = set(features.get("occasion", set()))
  scenario_norm = _norm_feature(scenario)

  if fit is not None:
    interests = {normalize_category(str(x).strip()) for x in (fit.interest_categories or []) if str(x).strip()}
    if interests and product_categories & interests:
      score += 0.20
      reasons.append("Совпадает с вашими интересами")
    fit_scenarios = {_norm_feature(x) for x in (fit.style_scenarios or []) if _norm_feature(x)}
    if fit_scenarios and product_styles & fit_scenarios:
      score += 0.14
      reasons.append("Подходит под ваш сценарий")
    if scenario_norm and scenario_norm in product_occasion:
      score += 0.16
      reasons.append("Подходит под выбранный повод")
  if scenario_norm == "daily" and {"casual", "minimalism", "basic"} & product_styles:
    score += 0.08
  return _clamp01(score), reasons, {"scenario_match": _clamp01(score)}


def compute_quality_score(product: Product) -> tuple[float, list[str], dict[str, float]]:
  score = 0.0
  if product.title:
    score += 0.12
  if product.brand:
    score += 0.10
  if product.category:
    score += 0.12
  if product.image_url:
    score += 0.18
  if product.affiliate_url or product.product_url:
    score += 0.16
  if int(product.price or 0) > 0:
    score += 0.10
  if product.colors:
    score += 0.08
  if product.style_tags:
    score += 0.08
  if product.available_sizes:
    score += 0.06
  if product.image_quality_score is not None:
    score = score * 0.85 + _clamp01(float(product.image_quality_score)) * 0.15
  if not bool(product.is_available) or not bool(product.is_active):
    score = min(score, 0.2)
  reasons = ["Карточка товара хорошо заполнена"] if score >= 0.75 else []
  return _clamp01(score), reasons, {"catalog_completeness": _clamp01(score)}


def compute_exploration_score(
  product: Product,
  *,
  taste_features: TasteFeatureMap,
  seen_count: int,
) -> tuple[float, list[str], dict[str, float]]:
  confidence = profile_confidence(taste_features)
  score = 0.65 * (1.0 - confidence) + 0.25
  if seen_count > 0:
    score -= min(0.30, seen_count * 0.08)
  features = product_taste_features(product)
  has_known_positive = False
  for feature_type, values in features.items():
    for value in values:
      pref, conf = taste_features.get((feature_type, str(value).lower()), (0.0, 0.0))
      if pref > 0 and conf > 0.4:
        has_known_positive = True
        break
    if has_known_positive:
      break
  if has_known_positive:
    score -= 0.10
  reasons = ["Немного расширяет подборку"] if score >= 0.55 else []
  return _clamp01(score), reasons, {"profile_confidence": confidence}


def compute_mie_score(
  product: Product,
  *,
  taste: TasteProfile,
  fit: FitProfile | None,
  palette: set[str],
  avoid_colors: set[str],
  taste_features: TasteFeatureMap,
  seen_count: int = 0,
  popularity: float = 0.0,
  scenario: str = "daily",
) -> MIEScore:
  fit_score, hard_reject, fit_reasons, fit_debug = compute_fit_score(
    product,
    fit=fit,
    palette=palette,
    avoid_colors=avoid_colors,
  )
  taste_score, taste_reasons, taste_debug = compute_taste_score(
    product,
    taste=taste,
    taste_features=taste_features,
  )
  context_score, context_reasons, context_debug = compute_context_score(
    product,
    fit=fit,
    scenario=scenario,
  )
  quality_score, quality_reasons, quality_debug = compute_quality_score(product)
  exploration_score, exploration_reasons, exploration_debug = compute_exploration_score(
    product,
    taste_features=taste_features,
    seen_count=seen_count,
  )

  weighted = (
    FINAL_SCORE_WEIGHTS["taste_score"] * taste_score
    + FINAL_SCORE_WEIGHTS["fit_score"] * fit_score
    + FINAL_SCORE_WEIGHTS["context_score"] * context_score
    + FINAL_SCORE_WEIGHTS["quality_score"] * quality_score
    + FINAL_SCORE_WEIGHTS["exploration_score"] * exploration_score
  )
  popularity_prior = min(0.04, max(0.0, popularity) / 250.0)
  final = 0.0 if hard_reject else _clamp01(weighted + popularity_prior)

  reasons: list[str] = []
  for reason in [
    *fit_reasons,
    *taste_reasons,
    *context_reasons,
    *quality_reasons,
    *exploration_reasons,
  ]:
    if reason and reason not in reasons:
      reasons.append(reason)

  debug = {
    **fit_debug,
    **taste_debug,
    **context_debug,
    **quality_debug,
    **exploration_debug,
    "popularity": float(popularity),
    "popularity_prior": popularity_prior,
    "fit_min_threshold": FIT_MIN_THRESHOLD,
    "weighted_score": weighted,
  }
  return MIEScore(
    final_score=final,
    fit_score=fit_score,
    taste_score=taste_score,
    context_score=context_score,
    quality_score=quality_score,
    exploration_score=exploration_score,
    hard_reject=hard_reject,
    reasons=reasons,
    debug=debug,
  )
