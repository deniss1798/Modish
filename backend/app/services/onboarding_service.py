"""Онбординг v2: шаги 1–3, синхронизация с FitProfile, Wow-образы."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import business
from ..catalog_normalize import normalize_category
from ..models import FitProfile, StyleProfile, User
from ..schemas.photo_analysis import build_profile_json_after_analysis
from .outfit_service import generate_outfits
from .photo_analysis import (
  BODY_SHAPE_OPTIONS,
  COLOR_TYPE_OPTIONS,
  HEIGHT_CATEGORY_OPTIONS,
)
from .recommendation_engine import ensure_style_profile

PRICE_SEGMENT_BUDGET: dict[str, tuple[int, int]] = {
  "economy": (0, 4_000),
  "mass": (0, 10_000),
  "mid": (0, 25_000),
  "premium": (0, 80_000),
}

STYLE_PREF_TO_SCENARIO: dict[str, str] = {
  "casual": "daily",
  "minimalism": "minimal",
  "minimal": "minimal",
  "smart_casual": "office",
  "elegant": "evening",
  "streetwear": "casual",
  "romantic": "evening",
  "business": "office",
  "sporty": "daily",
  "sport": "daily",
}

STYLE_PREF_TO_CATEGORIES: dict[str, list[str]] = {
  "casual": ["футболки", "джинсы", "брюки"],
  "minimalism": ["рубашки", "брюки"],
  "smart_casual": ["рубашки", "брюки", "верхний_слой"],
  "elegant": ["рубашки", "верхний_слой"],
  "streetwear": ["футболки", "верхний_слой", "джинсы"],
  "romantic": ["платья", "рубашки"],
  "business": ["рубашки", "брюки", "верхний_слой"],
  "sporty": ["спорт", "футболки"],
}


def _gender_to_style_target(gender: str | None) -> str:
  g = (gender or "").strip().lower()
  if g in ("female", "woman", "women", "womenswear", "женщина", "ж"):
    return "womenswear"
  if g in ("male", "man", "men", "menswear", "мужчина", "м"):
    return "menswear"
  return "unknown"


def _ensure_fit_profile(db: Session, user: User) -> FitProfile:
  fp = db.execute(select(FitProfile).where(FitProfile.user_id == user.id)).scalar_one_or_none()
  if fp is not None:
    return fp
  fp = FitProfile(
    id=str(uuid4()),
    user_id=user.id,
    height_cm=user.height_cm or 170,
    weight_kg=user.weight_kg,
    gender_target="unisex",
    clothing_size="M",
    budget_min=0,
    budget_max=10_000,
  )
  db.add(fp)
  db.flush()
  return fp


def _apply_analysis_to_fit(fp: FitProfile, analysis: dict[str, Any]) -> None:
  fp.body_proportions = str(analysis.get("body_proportions") or fp.body_proportions or "")
  fp.contrast_level = str(analysis.get("contrast_level") or fp.contrast_level or "")
  fp.color_palette = [str(x) for x in (analysis.get("color_palette") or []) if str(x).strip()]
  fp.avoid_colors = [str(x) for x in (analysis.get("avoid_colors") or []) if str(x).strip()]
  fp.recommended_silhouettes = [
    str(x) for x in (analysis.get("recommended_silhouettes") or []) if str(x).strip()
  ]
  fp.avoid_silhouettes = [
    str(x) for x in (analysis.get("avoid_silhouettes") or []) if str(x).strip()
  ]


def apply_step1(
  db: Session,
  *,
  user: User,
  profile: StyleProfile,
  gender: str,
  age_group: str | None,
) -> dict[str, Any]:
  style_target = _gender_to_style_target(gender)
  profile.style_target = style_target
  profile.age_group = (age_group or "").strip() or None
  profile.onboarding_step = max(profile.onboarding_step or 0, 1)
  profile.updated_at = datetime.now(timezone.utc)

  fp = _ensure_fit_profile(db, user)
  fp.gender_target = style_target
  fp.updated_at = datetime.now(timezone.utc)
  db.flush()
  return {
    "onboarding_step": profile.onboarding_step,
    "gender": gender,
    "style_target": style_target,
    "age_group": profile.age_group,
  }


def apply_photo_analysis_pending(
  db: Session,
  *,
  profile: StyleProfile,
  pending: dict[str, Any],
) -> dict[str, Any]:
  profile.photo_analysis = pending.get("photo_analysis")
  profile.body_shape = pending.get("body_shape")
  profile.color_type = pending.get("color_type")
  profile.height_category = pending.get("height_category")
  profile.onboarding_step = max(profile.onboarding_step or 0, 2)
  profile.updated_at = datetime.now(timezone.utc)
  db.flush()
  return {
    "status": "pending_confirmation",
    "body_shape": profile.body_shape,
    "color_type": profile.color_type,
    "height_category": profile.height_category,
    "summary": pending.get("summary", ""),
    "options": pending.get("options")
    or {
      "body_shapes": list(BODY_SHAPE_OPTIONS),
      "color_types": list(COLOR_TYPE_OPTIONS),
      "height_categories": list(HEIGHT_CATEGORY_OPTIONS),
    },
  }


def apply_photo_confirm(
  db: Session,
  *,
  user: User,
  profile: StyleProfile,
  body_shape: str | None,
  color_type: str | None,
  height_category: str | None,
  confirmed: bool = True,
) -> dict[str, Any]:
  if body_shape:
    profile.body_shape = body_shape.strip()
  if color_type:
    profile.color_type = color_type.strip()
  if height_category is not None:
    profile.height_category = height_category.strip() or None

  raw = profile.photo_analysis if isinstance(profile.photo_analysis, dict) else {}
  if raw:
    profile.profile_json = build_profile_json_after_analysis(
      raw,
      source="onboarding_v2_photo",
    )
    profile.confidence_score = 0.8 if confirmed else 0.6

  fp = _ensure_fit_profile(db, user)
  if isinstance(raw, dict) and raw:
    _apply_analysis_to_fit(fp, raw)
  if profile.body_shape:
    fp.body_proportions = profile.body_shape
  fp.updated_at = datetime.now(timezone.utc)

  profile.onboarding_step = max(profile.onboarding_step or 0, 2)
  profile.updated_at = datetime.now(timezone.utc)
  db.flush()
  return {
    "status": "confirmed",
    "body_shape": profile.body_shape,
    "color_type": profile.color_type,
    "height_category": profile.height_category,
    "onboarding_step": profile.onboarding_step,
  }


def apply_step3(
  db: Session,
  *,
  user: User,
  profile: StyleProfile,
  style_preferences: list[str] | None,
  price_segment: str | None,
) -> dict[str, Any]:
  prefs = [str(x).strip().lower() for x in (style_preferences or []) if str(x).strip()]
  profile.style_preferences = prefs or []
  if price_segment:
    profile.price_segment = price_segment.strip().lower()
  profile.onboarding_step = max(profile.onboarding_step or 0, 3)
  profile.updated_at = datetime.now(timezone.utc)

  fp = _ensure_fit_profile(db, user)
  cats: list[str] = []
  scenarios: list[str] = []
  for p in prefs:
    cats.extend(STYLE_PREF_TO_CATEGORIES.get(p, []))
    sc = STYLE_PREF_TO_SCENARIO.get(p)
    if sc and sc not in scenarios:
      scenarios.append(sc)
  if cats:
    fp.interest_categories = list(dict.fromkeys(normalize_category(c) or c for c in cats))
  if scenarios:
    fp.style_scenarios = scenarios
  seg = profile.price_segment or "mass"
  bmin, bmax = PRICE_SEGMENT_BUDGET.get(seg, PRICE_SEGMENT_BUDGET["mass"])
  fp.budget_min = bmin
  fp.budget_max = bmax
  fp.updated_at = datetime.now(timezone.utc)
  db.flush()
  return {
    "onboarding_step": profile.onboarding_step,
    "style_preferences": profile.style_preferences,
    "price_segment": profile.price_segment,
  }


def _wow_explanation(
  *,
  title: str,
  body_shape: str | None,
  color_type: str | None,
) -> str:
  parts = [f"«{title}»"]
  if color_type:
    parts.append(f"под цветотип {color_type}")
  if body_shape:
    parts.append(f"и тип фигуры {body_shape}")
  return " — ".join(parts) + "."


def complete_onboarding_wow(
  db: Session,
  *,
  user: User,
  profile: StyleProfile,
  outfit_count: int = 4,
) -> dict[str, Any]:
  profile.completed_at = datetime.now(timezone.utc)
  profile.onboarding_step = 4
  profile.updated_at = datetime.now(timezone.utc)

  scenario = "daily"
  prefs = profile.style_preferences or []
  if prefs:
    scenario = STYLE_PREF_TO_SCENARIO.get(prefs[0], "daily")

  outfits = generate_outfits(db, user, count=min(4, max(1, outfit_count)), scenario=scenario)
  body_shape = profile.body_shape
  color_type = profile.color_type

  from ..api.serializers import outfit_to_api
  from ..schemas.api_product import product_to_api as _product_to_api
  from sqlalchemy import select as _select
  from ..models import Product as _Product

  def _products_for_outfit(o):
    products: dict[str, Any] = {}
    for slot, pid in (o.items_json or {}).items():
      p = db.execute(_select(_Product).where(_Product.id == str(pid))).scalar_one_or_none()
      if p is not None:
        products[str(slot)] = _product_to_api(p)
    return products

  wow_outfits: list[dict[str, Any]] = []
  for o in outfits:
    api_o = outfit_to_api(o, _products_for_outfit(o))
    label = (o.style_direction or o.reason or "Образ").strip()
    api_o["title"] = label
    api_o["explanation"] = _wow_explanation(
      title=label,
      body_shape=body_shape,
      color_type=color_type,
    )
    wow_outfits.append(api_o)

  business.rebuild_user_summary(db, user.id)
  db.flush()
  return {
    "completed": True,
    "completed_at": profile.completed_at.isoformat(),
    "outfits": wow_outfits,
    "scenario": scenario,
  }


def onboarding_status(profile: StyleProfile) -> dict[str, Any]:
  return {
    "onboarding_step": profile.onboarding_step or 0,
    "completed": profile.completed_at is not None,
    "completed_at": profile.completed_at.isoformat() if profile.completed_at else None,
    "gender_style_target": profile.style_target,
    "age_group": profile.age_group,
    "body_shape": profile.body_shape,
    "color_type": profile.color_type,
    "style_preferences": profile.style_preferences or [],
    "price_segment": profile.price_segment,
  }
