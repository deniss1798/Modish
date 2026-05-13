"""Профиль пользователя, стиль, сохранённые товары, метрики, биллинг."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import business
from ..models import (
  FitProfile,
  MetricEvent,
  RecommendationEventV2,
  Product,
  StyleProfile,
  User,
)
from ..schemas.photo_analysis import build_profile_json_after_analysis
from ..services.style_analysis_service import analyze_photo_bytes as analyze_photo_ai
from .deps import auth_scheme, get_db, user_from_token
from .serializers import as_user_payload, fit_to_api, taste_to_api
from .upload_utils import read_upload_bytes_limited
from ..services.recommendation_engine import ensure_taste_profile

router = APIRouter(tags=["profile"])

MAX_PHOTO_BYTES = business.MAX_PHOTO_BYTES


class StyleTargetRequest(BaseModel):
  style_target: str = Field(pattern="^(menswear|womenswear|unisex|unknown)$")


class StyleProfilePatchRequest(BaseModel):
  profile_json: dict[str, Any] | None = None
  style_target: str | None = None


class UserPreferencesPatchRequest(BaseModel):
  height: int = Field(ge=50, le=250)
  weight: int | None = Field(default=None, ge=20, le=400)
  fit_preference: str = Field(pattern="^(slim|regular|oversized)$")


class MetricEventRequest(BaseModel):
  name: str = Field(pattern="^[a-z_]{3,64}$")
  meta: dict[str, Any] | None = None


@router.get("/users/me")
def users_me(
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, Any]:
  user = user_from_token(credentials, db)
  return as_user_payload(user)


@router.get("/profile/brief")
def profile_brief(
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, Any]:
  user = user_from_token(credentials, db)
  fp = db.execute(select(FitProfile).where(FitProfile.user_id == user.id)).scalar_one_or_none()
  tp = ensure_taste_profile(db, user.id)
  events = db.execute(
    select(func.count()).select_from(RecommendationEventV2).where(RecommendationEventV2.user_id == user.id)
  ).scalar_one()
  events = int(events or 0)
  saved = db.execute(
    select(func.count()).select_from(RecommendationEventV2).where(
      RecommendationEventV2.user_id == user.id,
      RecommendationEventV2.event_type == "save",
    )
  ).scalar_one()
  saved = int(saved or 0)
  return {
    "fit_profile": fit_to_api(fp) if fp else None,
    "taste_profile": taste_to_api(tp),
    "stats": {"events": events, "saved": saved},
    "next": "keep_swiping" if events < 20 else "check_outfits",
  }


@router.post("/metrics/events")
def metrics_events(
  payload: MetricEventRequest,
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, Any]:
  user = user_from_token(credentials, db)
  from uuid import uuid4

  ev = MetricEvent(
    id=str(uuid4()),
    user_id=user.id,
    name=payload.name,
    meta_json=payload.meta or {},
    created_at=datetime.now(timezone.utc),
  )
  db.add(ev)
  db.commit()
  return {"status": "ok"}


@router.get("/saved-products")
def saved_products(
  limit: int = 50,
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> list[dict[str, Any]]:
  from ..schemas.api_product import product_to_api

  user = user_from_token(credentials, db)
  rows = db.execute(
    select(RecommendationEventV2, Product)
    .join(Product, Product.id == RecommendationEventV2.product_id)
    .where(
      RecommendationEventV2.user_id == user.id,
      RecommendationEventV2.event_type.in_(["save", "unsave"]),
      RecommendationEventV2.product_id.is_not(None),
    )
    .order_by(RecommendationEventV2.created_at.desc())
    .limit(min(400, max(1, limit * 4)))
  ).all()
  # У каждого товара берём только последнее событие save/unsave (порядок — от новых к старым).
  last_by_product: dict[str, tuple[RecommendationEventV2, Product]] = {}
  for ev, p in rows:
    pid = str(p.id)
    if pid in last_by_product:
      continue
    last_by_product[pid] = (ev, p)
  out: list[dict[str, Any]] = [
    {
      "saved_at": ev.created_at.isoformat(),
      "product": product_to_api(p),
    }
    for _pid, (ev, p) in last_by_product.items()
    if ev.event_type == "save"
  ]
  out.sort(key=lambda x: x["saved_at"], reverse=True)
  return out[: min(200, max(1, limit))]


@router.patch("/users/me/preferences")
def users_me_preferences_patch(
  payload: UserPreferencesPatchRequest,
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, Any]:
  user = user_from_token(credentials, db)
  user.height_cm = int(payload.height)
  user.weight_kg = int(payload.weight) if payload.weight is not None else None
  user.fit_preference = payload.fit_preference
  user.updated_at = datetime.now(timezone.utc)
  db.commit()
  return as_user_payload(user)


@router.get("/billing/status")
def billing_status(
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, Any]:
  user = user_from_token(credentials, db)
  return business.billing_status_payload(db, user)


@router.get("/style-profile/me")
def style_profile_me(
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, Any]:
  user = user_from_token(credentials, db)
  profile = db.execute(select(StyleProfile).where(StyleProfile.user_id == user.id)).scalar_one()
  return {
    "id": profile.id,
    "user_id": profile.user_id,
    "style_target": profile.style_target,
    "confidence_score": profile.confidence_score,
    "profile_json": profile.profile_json,
    "updated_at": profile.updated_at.isoformat(),
  }


@router.patch("/style-profile/me")
def style_profile_patch(
  payload: StyleProfilePatchRequest,
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, Any]:
  user = user_from_token(credentials, db)
  profile = db.execute(select(StyleProfile).where(StyleProfile.user_id == user.id)).scalar_one()
  if payload.style_target is not None:
    if payload.style_target not in {"menswear", "womenswear", "unisex", "unknown"}:
      raise HTTPException(status_code=400, detail="Некорректный style_target")
    profile.style_target = payload.style_target
  if payload.profile_json is not None:
    merged = dict(profile.profile_json or {})
    merged.update(payload.profile_json)
    profile.profile_json = merged
  profile.updated_at = datetime.now(timezone.utc)
  db.commit()
  business.rebuild_user_summary(db, user.id)
  db.commit()
  return {
    "id": profile.id,
    "user_id": profile.user_id,
    "style_target": profile.style_target,
    "confidence_score": profile.confidence_score,
    "profile_json": profile.profile_json,
  }


@router.patch("/style-profile/target")
def patch_style_target(
  payload: StyleTargetRequest,
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, Any]:
  user = user_from_token(credentials, db)
  profile = db.execute(select(StyleProfile).where(StyleProfile.user_id == user.id)).scalar_one()
  profile.style_target = payload.style_target
  profile.updated_at = datetime.now(timezone.utc)
  db.commit()
  business.rebuild_user_summary(db, user.id)
  db.commit()
  return {
    "id": profile.id,
    "user_id": profile.user_id,
    "style_target": profile.style_target,
    "confidence_score": profile.confidence_score,
    "profile_json": profile.profile_json,
  }


@router.post("/style-profile/analyze")
async def analyze(
  photo: UploadFile = File(...),
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, Any]:
  ct = (photo.content_type or "").lower()
  if ct not in {"image/jpeg", "image/jpg", "image/png", "image/webp"}:
    raise HTTPException(status_code=400, detail="Допустимы jpg, jpeg, png, webp")
  user = user_from_token(credentials, db)
  try:
    limits = business.assert_can_analyze(db, user)
  except PermissionError as e:
    raise HTTPException(status_code=403, detail=str(e)) from e
  raw = await read_upload_bytes_limited(photo, MAX_PHOTO_BYTES)
  profile = db.execute(select(StyleProfile).where(StyleProfile.user_id == user.id)).scalar_one()
  try:
    analysis = await analyze_photo_ai(content_type=ct, raw=raw)
  except RuntimeError as e:
    raise HTTPException(status_code=502, detail=str(e)) from e
  profile.profile_json = build_profile_json_after_analysis(
    analysis,
    source="photo_analysis_v2_openai",
  )
  profile.confidence_score = 0.75
  profile.updated_at = datetime.now(timezone.utc)
  limits.photo_analysis_used += 1
  limits.updated_at = datetime.now(timezone.utc)
  db.commit()
  business.rebuild_user_summary(db, user.id)
  db.commit()
  return {
    "id": profile.id,
    "user_id": profile.user_id,
    "style_target": profile.style_target,
    "confidence_score": profile.confidence_score,
    "profile_json": profile.profile_json,
  }
