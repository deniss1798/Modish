"""Сериализация сущностей в JSON для REST."""
from __future__ import annotations

from typing import Any

from ..catalog_normalize import normalize_interest_category, normalize_style_scenario
from ..models import FitProfile, Outfit, Recommendation, TasteProfile


def as_user_payload(user: Any) -> dict[str, Any]:
  return {
    "id": user.id,
    "email": user.email,
    "plan": user.plan,
    "subscription_status": user.subscription_status,
    "height_cm": user.height_cm,
    "weight_kg": user.weight_kg,
    "fit_preference": user.fit_preference,
    "trial_started_at": user.trial_started_at.isoformat(),
    "trial_ends_at": user.trial_ends_at.isoformat(),
  }


def norm_fit_tag_list(raw: list[str], *, max_items: int = 24) -> list[str]:
  out: list[str] = []
  for x in raw[:max_items]:
    s = normalize_interest_category(str(x).strip())
    if s and s not in out:
      out.append(s)
  return out


def norm_style_scenario_list(raw: list[str], *, max_items: int = 24) -> list[str]:
  out: list[str] = []
  for x in raw[:max_items]:
    s = normalize_style_scenario(str(x).strip())
    if s and s not in out:
      out.append(s)
  return out


def fit_to_api(fp: FitProfile) -> dict[str, Any]:
  return {
    "height": fp.height_cm,
    "weight": fp.weight_kg,
    "gender_target": fp.gender_target,
    "clothing_size": fp.clothing_size,
    "body_proportions": fp.body_proportions,
    "contrast_level": fp.contrast_level,
    "color_palette": fp.color_palette or [],
    "avoid_colors": fp.avoid_colors or [],
    "recommended_silhouettes": fp.recommended_silhouettes or [],
    "avoid_silhouettes": fp.avoid_silhouettes or [],
    "recommended_fit": fp.recommended_fit,
    "avoid_fit": fp.avoid_fit or [],
    "style_constraints": fp.style_constraints or {},
    "interest_categories": fp.interest_categories or [],
    "style_scenarios": fp.style_scenarios or [],
    "budget_min": fp.budget_min,
    "budget_max": fp.budget_max,
    "updated_at": fp.updated_at.isoformat(),
  }


def taste_to_api(tp: TasteProfile) -> dict[str, Any]:
  def top_keys(d: dict[str, Any], *, sign: int, limit: int = 12) -> list[str]:
    items: list[tuple[str, int]] = []
    for k, v in (d or {}).items():
      try:
        iv = int(v)
      except Exception:
        continue
      if sign > 0 and iv > 0:
        items.append((str(k), iv))
      if sign < 0 and iv < 0:
        items.append((str(k), iv))
    items.sort(key=lambda x: abs(x[1]), reverse=True)
    return [k for k, _ in items[:limit]]

  return {
    "liked_categories": top_keys(tp.category_weights or {}, sign=+1) or (tp.liked_categories or []),
    "disliked_categories": top_keys(tp.category_weights or {}, sign=-1) or (tp.disliked_categories or []),
    "liked_colors": top_keys(tp.color_weights or {}, sign=+1) or (tp.liked_colors or []),
    "disliked_colors": top_keys(tp.color_weights or {}, sign=-1) or (tp.disliked_colors or []),
    "liked_brands": top_keys(tp.brand_weights or {}, sign=+1) or (tp.liked_brands or []),
    "disliked_brands": top_keys(tp.brand_weights or {}, sign=-1) or (tp.disliked_brands or []),
    "liked_styles": top_keys(tp.style_weights or {}, sign=+1) or (tp.liked_styles or []),
    "disliked_styles": top_keys(tp.style_weights or {}, sign=-1) or (tp.disliked_styles or []),
    "weights": {
      "categories": tp.category_weights or {},
      "brands": tp.brand_weights or {},
      "colors": tp.color_weights or {},
      "styles": tp.style_weights or {},
    },
    "price_range": {"min": tp.price_min, "max": tp.price_max},
    "preferred_fit": tp.preferred_fit,
    "updated_at": tp.updated_at.isoformat(),
  }


def outfit_to_api(o: Outfit, products: dict[str, Any] | None = None) -> dict[str, Any]:
  items = dict(o.items_json or {})
  return {
    "id": o.id,
    "items": items,
    "products": products or {},
    "total_price": o.total_price,
    "style_direction": o.style_direction,
    "reason": o.reason,
    "score": o.score,
    "is_saved": bool(o.is_saved),
    "created_at": o.created_at.isoformat(),
  }


def rec_to_dict(item: Recommendation) -> dict[str, Any]:
  return {
    "id": item.id,
    "type": item.type,
    "title": item.title,
    "description": item.description,
    "content_json": item.content_json,
    "tags_json": item.tags_json,
    "status": item.status,
    "created_at": item.created_at.isoformat(),
  }
