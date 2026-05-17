from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import FitProfile, User
from .deps import auth_scheme, get_db, user_from_token
from .serializers import fit_to_api, norm_fit_tag_list, norm_style_scenario_list

router = APIRouter(tags=["fit-profile"])


class FitProfilePatchRequest(BaseModel):
  height: int = Field(ge=50, le=250)
  weight: int | None = Field(default=None, ge=20, le=400)
  gender_target: str = Field(pattern="^(menswear|womenswear|unisex)$")
  clothing_size: str = Field(min_length=1, max_length=16)
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
      clothing_size="M",
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
  return fit_to_api(fp)


@router.patch("/fit-profile/me")
def fit_profile_patch(
  payload: FitProfilePatchRequest,
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict:
  user = user_from_token(credentials, db)
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
    fp.clothing_size = payload.clothing_size
    fp.budget_min = payload.budget_min
    fp.budget_max = payload.budget_max
    fp.interest_categories = norm_fit_tag_list(payload.interest_categories)
    fp.style_scenarios = norm_style_scenario_list(payload.style_scenarios)
    fp.updated_at = now
  db.commit()
  return fit_to_api(fp)
