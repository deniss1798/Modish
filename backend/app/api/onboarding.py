"""Онбординг v2: 3 шага + Wow."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import business
from ..api.serializers import outfit_to_api
from ..models import Product, StyleProfile
from ..schemas.api_product import product_to_api
from ..services.onboarding_service import (
  apply_photo_analysis_pending,
  apply_photo_confirm,
  apply_step1,
  apply_step3,
  complete_onboarding_wow,
  onboarding_status,
)
from ..services.photo_analysis import analyze_onboarding_photo
from ..services.recommendation_engine import ensure_style_profile
from .deps import auth_scheme, get_db, user_from_token
from .upload_utils import read_upload_bytes_limited
from sqlalchemy import select

router = APIRouter(prefix="/onboarding", tags=["onboarding"])

MAX_PHOTO_BYTES = business.MAX_PHOTO_BYTES


class OnboardingStep1Request(BaseModel):
  gender: str = Field(description="female | male")
  age_group: str | None = Field(
    default=None,
    description="18-24 | 25-34 | 35-44 | 45+",
  )


class OnboardingPhotoConfirmRequest(BaseModel):
  body_shape: str | None = None
  color_type: str | None = None
  height_category: str | None = None
  confirmed: bool = True


class OnboardingStep3Request(BaseModel):
  style_preferences: list[str] | None = None
  price_segment: str | None = Field(
    default=None,
    description="economy | mass | mid | premium",
  )


def _profile(db: Session, user_id: str) -> StyleProfile:
  return ensure_style_profile(db, user_id)


def _outfit_products(db: Session, o) -> dict[str, Any]:
  products: dict[str, Any] = {}
  for slot, pid in (o.items_json or {}).items():
    p = db.execute(select(Product).where(Product.id == str(pid))).scalar_one_or_none()
    if p is not None:
      products[str(slot)] = product_to_api(p)
  return products


@router.get("/status")
def get_onboarding_status(
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, Any]:
  user = user_from_token(credentials, db)
  profile = _profile(db, user.id)
  return onboarding_status(profile)


@router.patch("/step1")
def onboarding_step1(
  payload: OnboardingStep1Request,
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, Any]:
  user = user_from_token(credentials, db)
  g = payload.gender.strip().lower()
  if g not in ("female", "male"):
    raise HTTPException(status_code=400, detail="gender: female или male")
  if payload.age_group and payload.age_group not in ("18-24", "25-34", "35-44", "45+"):
    raise HTTPException(status_code=400, detail="Некорректная age_group")
  profile = _profile(db, user.id)
  result = apply_step1(db, user=user, profile=profile, gender=g, age_group=payload.age_group)
  db.commit()
  return result


@router.post("/photo")
async def onboarding_photo(
  photo: UploadFile = File(...),
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, Any]:
  """Загрузка фото + AI-анализ → экран подтверждения (ещё не финализировано)."""
  ct = (photo.content_type or "").lower()
  if ct not in {"image/jpeg", "image/jpg", "image/png", "image/webp"}:
    raise HTTPException(status_code=400, detail="Допустимы jpg, jpeg, png, webp")
  user = user_from_token(credentials, db)
  try:
    limits = business.assert_can_analyze(db, user)
  except PermissionError as e:
    raise HTTPException(status_code=403, detail=str(e)) from e
  raw = await read_upload_bytes_limited(photo, MAX_PHOTO_BYTES)
  profile = _profile(db, user.id)
  try:
    pending = await analyze_onboarding_photo(content_type=ct, raw=raw)
  except RuntimeError as e:
    raise HTTPException(status_code=502, detail=str(e)) from e
  result = apply_photo_analysis_pending(db, profile=profile, pending=pending)
  limits.photo_analysis_used += 1
  from datetime import datetime, timezone

  limits.updated_at = datetime.now(timezone.utc)
  db.commit()
  return result


@router.patch("/photo/confirm")
def onboarding_photo_confirm(
  payload: OnboardingPhotoConfirmRequest,
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, Any]:
  user = user_from_token(credentials, db)
  profile = _profile(db, user.id)
  if not profile.photo_analysis:
    raise HTTPException(status_code=400, detail="Сначала загрузите фото (POST /onboarding/photo)")
  result = apply_photo_confirm(
    db,
    user=user,
    profile=profile,
    body_shape=payload.body_shape,
    color_type=payload.color_type,
    height_category=payload.height_category,
    confirmed=payload.confirmed,
  )
  business.rebuild_user_summary(db, user.id)
  db.commit()
  return result


@router.patch("/step3")
def onboarding_step3(
  payload: OnboardingStep3Request,
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, Any]:
  user = user_from_token(credentials, db)
  seg = (payload.price_segment or "").strip().lower() or None
  if seg and seg not in ("economy", "mass", "mid", "premium"):
    raise HTTPException(status_code=400, detail="price_segment: economy|mass|mid|premium")
  profile = _profile(db, user.id)
  result = apply_step3(
    db,
    user=user,
    profile=profile,
    style_preferences=payload.style_preferences,
    price_segment=seg,
  )
  db.commit()
  return result


@router.post("/complete")
def onboarding_complete(
  count: int = 4,
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, Any]:
  """Wow moment: 3–4 готовых образа + завершение онбординга."""
  user = user_from_token(credentials, db)
  profile = _profile(db, user.id)
  if profile.onboarding_step < 2 and not profile.body_shape:
    raise HTTPException(status_code=400, detail="Завершите шаг с фото")
  try:
    result = complete_onboarding_wow(db, user=user, profile=profile, outfit_count=count)
    db.commit()
  except Exception as exc:
    db.rollback()
    raise HTTPException(status_code=500, detail=f"Не удалось собрать образы: {exc!s}") from exc
  return result
