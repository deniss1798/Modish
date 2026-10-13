from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ActivationSession, FitProfile, User
from ..schemas.fit_sizes import FitSizesInput, BodyMeasurementsInput
from ..services.fit_size_profile import submitted_sizes, update_size_profile, size_profile_to_api
from .deps import auth_scheme, get_db, user_from_token
from .serializers import fit_to_api, norm_fit_tag_list, norm_style_scenario_list

router = APIRouter(tags=["fit-profile"])


def _response(fp, activation):
  shoe = size_profile_to_api(fp)['sizes']['shoes']
  return {**fit_to_api(fp), 'shoe_size': shoe['value'] if shoe.get('state') == 'confirmed' else None,
          'activation_segment': activation.segment if activation else None}


class FitProfilePatchRequest(BaseModel):
  height: int = Field(ge=50, le=250)
  weight: int | None = Field(default=None, ge=20, le=400)
  gender_target: str = Field(pattern="^(menswear|womenswear|unisex)$")
  clothing_size: str | None = Field(default=None, max_length=16)
  sizes: FitSizesInput | None = None
  body_measurements: BodyMeasurementsInput | None = None
  shoe_size: str | None = Field(default=None, max_length=16)
  clothing_size_system: str | None = Field(default=None, pattern='^(RU|EU|US|UK|LETTER)$')
  shoe_size_system: str | None = Field(default=None, pattern='^(EU|RU|US|UK)$')
  budget_min: int = Field(default=0, ge=0)
  budget_max: int = Field(default=10000, ge=0)
  interest_categories: list[str] = Field(default_factory=list, max_length=24)
  style_scenarios: list[str] = Field(default_factory=list, max_length=24)


@router.get("/fit-profile/me")
def fit_profile_me(
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict:
  user = user_from_token(credentials, db)
  fp = db.execute(select(FitProfile).where(FitProfile.user_id == user.id)).scalar_one_or_none()
  if fp is None:
    now = datetime.now(timezone.utc)
    fp = FitProfile(
      id=str(uuid4()),
      user_id=user.id,
      height_cm=user.height_cm,
      weight_kg=user.weight_kg,
      gender_target="unisex",
      clothing_size=None,
      body_proportions="",
      contrast_level="",
      color_palette=[],
      avoid_colors=[],
      recommended_silhouettes=[],
      avoid_silhouettes=[],
      recommended_fit=user.fit_preference,
      avoid_fit=[],
      style_constraints={},
      interest_categories=[],
      style_scenarios=[],
      budget_min=0,
      budget_max=10000,
      created_at=now,
      updated_at=now,
    )
    db.add(fp)
    db.commit()
  activation = db.get(ActivationSession, user.id)
  return _response(fp, activation)


@router.patch("/fit-profile/me")
def fit_profile_patch(
  payload: FitProfilePatchRequest,
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict:
  user = user_from_token(credentials, db)
  from ..services.activation_service import lock_user
  lock_user(db, user)
  activation = db.get(ActivationSession, user.id)
  user.height_cm = payload.height
  user.weight_kg = payload.weight
  if activation and payload.gender_target != 'womenswear':
    raise HTTPException(status_code=400, detail='Новый подбор пока доступен для женского гардероба.')
  if activation and payload.shoe_size is not None:
    activation.shoe_size = payload.shoe_size
  fp = db.execute(select(FitProfile).where(FitProfile.user_id == user.id)).scalar_one_or_none()
  now = datetime.now(timezone.utc)
  if fp is None:
    fp = FitProfile(
      id=str(uuid4()),
      user_id=user.id,
      height_cm=payload.height,
      weight_kg=payload.weight,
      gender_target=payload.gender_target,
      clothing_size=payload.clothing_size,
      body_proportions="",
      contrast_level="",
      color_palette=[],
      avoid_colors=[],
      recommended_silhouettes=[],
      avoid_silhouettes=[],
      recommended_fit=user.fit_preference,
      avoid_fit=[],
      style_constraints={},
      interest_categories=norm_fit_tag_list(payload.interest_categories),
      style_scenarios=norm_style_scenario_list(payload.style_scenarios),
      budget_min=payload.budget_min,
      budget_max=payload.budget_max,
      created_at=now,
      updated_at=now,
    )
    db.add(fp)
  else:
    fp.height_cm = payload.height
    fp.weight_kg = payload.weight
    fp.gender_target = payload.gender_target
    if 'clothing_size' in payload.model_fields_set:
      fp.clothing_size = payload.clothing_size
    fp.budget_min = payload.budget_min
    fp.budget_max = payload.budget_max
    fp.interest_categories = norm_fit_tag_list(payload.interest_categories)
    fp.style_scenarios = norm_style_scenario_list(payload.style_scenarios)
    fp.updated_at = now
  constraints=dict(fp.style_constraints or {})
  if 'clothing_size_system' in payload.model_fields_set:
    constraints['clothing_size_system'] = payload.clothing_size_system
  if payload.shoe_size is not None:
    constraints['shoe_size_system']=payload.shoe_size_system
  fp.style_constraints=constraints
  update_size_profile(fp, sizes=submitted_sizes(
      **({'clothing_size': payload.clothing_size} if 'clothing_size' in payload.model_fields_set else {}),
      clothing_system=payload.clothing_size_system, shoe_size=payload.shoe_size,
      shoe_system=payload.shoe_size_system,
      overrides=payload.sizes.model_dump(exclude_unset=True) if payload.sizes else None), height_confirmed=True)
  if payload.body_measurements is not None:
    fp.body_measurements_json = {**(fp.body_measurements_json or {}),
        **payload.body_measurements.model_dump(exclude_unset=True)}
  db.commit()
  return _response(fp, activation)
