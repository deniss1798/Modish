"""Онбординг v2: 3 шага + Wow."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import business
from ..api.serializers import outfit_to_api
from ..models import ActivationSession, FitProfile, Product, StyleProfile
from ..schemas.api_product import product_to_api
from ..schemas.fit_sizes import FitSizesInput
from ..services.onboarding_service import (
  apply_photo_confirm,
  apply_step1,
  apply_step3,
  complete_onboarding_wow,
  onboarding_status,
)
from ..services.photo_analysis import analyze_onboarding_photo
from ..services.photo_analysis_jobs import run_photo_analysis
from ..services.recommendation_capabilities import recommendation_features
from ..services.recommendation_engine import ensure_style_profile
from .deps import auth_scheme, get_db, user_from_token
from .upload_utils import read_upload_bytes_limited
from sqlalchemy import select

router = APIRouter(prefix="/onboarding", tags=["onboarding"])

MAX_PHOTO_BYTES = business.MAX_PHOTO_BYTES


class ActivationPreferencesRequest(BaseModel):
  gender_target: str = Field(pattern='^(menswear|womenswear|unisex)$')
  age_group: str
  clothing_size: str = Field(max_length=16)
  shoe_size: str = Field(max_length=16)
  clothing_size_system: str | None = Field(default=None, pattern='^(RU|EU|US|UK|LETTER)$')
  shoe_size_system: str | None = Field(default=None, pattern='^(EU|RU|US|UK)$')
  styles: list[str] = Field(min_length=2, max_length=3)
  price_segment: str
  height: int = Field(ge=50, le=250)
  sizes: FitSizesInput | None = None


class StyleSwipeRequest(BaseModel):
  product_id: str = Field(max_length=36)
  reaction: str = Field(pattern='^(like|dislike|unavailable)$')


def _activation(db, user):
  from ..services.activation_service import lock_user
  lock_user(db, user)
  session = db.get(ActivationSession, user.id)
  if session is None:
    raise HTTPException(status_code=409, detail='Сначала укажите размеры, бюджет и стили.')
  return session


@router.post('/preferences')
def activation_preferences(payload: ActivationPreferencesRequest, db: Session = Depends(get_db), credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme)):
  from ..services.activation_service import configure
  user = user_from_token(credentials, db)
  configure(db, user, gender_target=payload.gender_target, age_group=payload.age_group, clothing_size=payload.clothing_size,
      shoe_size=payload.shoe_size, styles=payload.styles, price_segment=payload.price_segment,
      clothing_size_system=payload.clothing_size_system, shoe_size_system=payload.shoe_size_system,
      height=payload.height, sizes=payload.sizes.model_dump(exclude_unset=True) if payload.sizes else None)
  db.commit()
  return {'status': 'ready_for_swipe', 'activation_version': 1,
      'recommendation_features': recommendation_features(activation=True)}


@router.get('/style-swipe')
def get_style_swipe(db: Session = Depends(get_db), credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme)):
  from ..services.activation_deck_session import build_swipe_deck
  user = user_from_token(credentials, db)
  user_id, bind = user.id, db.get_bind()
  if db.new or db.dirty or db.deleted:
    raise RuntimeError('Style Swipe request cannot own uncommitted writes')
  db.close()
  return build_swipe_deck(bind, user_id)


@router.post('/style-swipe')
def post_style_swipe(payload: StyleSwipeRequest, db: Session = Depends(get_db), credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme)):
  from ..services.activation_service import swipe_feedback
  user = user_from_token(credentials, db)
  result = swipe_feedback(db, user, _activation(db, user), payload.product_id, payload.reaction)
  db.commit()
  return result


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
  from ..services.activation_service import legacy_has_taste, rated_count, MIN_SWIPES, SWIPE_TARGET
  activation = db.get(ActivationSession, user.id)
  fit = db.scalar(select(FitProfile).where(FitProfile.user_id == user.id))
  result = onboarding_status(profile)
  from ..services.fit_size_profile import missing_fit_requirements, size_profile_to_api
  missing_fit = missing_fit_requirements(fit)
  result.update(activation_version=1, completed=bool(activation.completed_at) if activation else legacy_has_taste(db, user, profile),
      recommendation_features=recommendation_features(activation=activation is not None),
      preferences_ready=activation is not None and not missing_fit, rated=rated_count(activation) if activation else 0,
      minimum=MIN_SWIPES, target=SWIPE_TARGET, clothing_size=fit.clothing_size if fit else None,
      shoe_size=activation.shoe_size if activation else None, gender_target=fit.gender_target if fit else None)
  result.update(fit_confirmation_required=bool(missing_fit), missing_fit_requirements=missing_fit,
      size_profile=size_profile_to_api(fit), height=fit.height_cm if fit and not 'height' in missing_fit else None)
  db.commit()
  return result


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
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
  idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
  """Загрузка фото + AI-анализ → экран подтверждения (ещё не финализировано)."""
  ct = (photo.content_type or "").lower()
  if ct not in {"image/jpeg", "image/jpg", "image/png", "image/webp"}:
    raise HTTPException(status_code=400, detail="Допустимы jpg, jpeg, png, webp")
  raw = await read_upload_bytes_limited(photo, MAX_PHOTO_BYTES)
  return await run_photo_analysis(
    credentials=credentials, request_id=idempotency_key, kind="onboarding",
    content_type=ct, raw=raw, provider=analyze_onboarding_photo,
  )


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
  """Complete only after durable taste learning; retries return the same Wow."""
  user = user_from_token(credentials, db)
  from ..services.wow_session import build_wow_response
  user_id, bind = user.id, db.get_bind()
  if db.new or db.dirty or db.deleted:
    raise RuntimeError('Wow request cannot own uncommitted writes')
  db.close()
  try:
    result = build_wow_response(bind, user_id)
  except HTTPException:
    db.rollback()
    raise
  except Exception as exc:
    db.rollback()
    raise HTTPException(status_code=500, detail="Не удалось собрать образы. Повторите попытку — оценки уже сохранены.") from exc
  return result
