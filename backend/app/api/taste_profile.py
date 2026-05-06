from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..services.recommendation_engine import ensure_taste_profile
from .deps import auth_scheme, get_db, user_from_token
from .serializers import taste_to_api

router = APIRouter(tags=["taste-profile"])


class TasteProfilePatchRequest(BaseModel):
  price_min: int = Field(default=0, ge=0)
  price_max: int = Field(default=10000, ge=0)
  preferred_fit: str = Field(pattern="^(slim|regular|oversized)$")


@router.get("/taste-profile/me")
def taste_profile_me(
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict:
  user = user_from_token(credentials, db)
  tp = ensure_taste_profile(db, user.id)
  db.commit()
  return taste_to_api(tp)


@router.patch("/taste-profile/me")
def taste_profile_patch(
  payload: TasteProfilePatchRequest,
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict:
  user = user_from_token(credentials, db)
  tp = ensure_taste_profile(db, user.id)
  tp.price_min = payload.price_min
  tp.price_max = payload.price_max
  tp.preferred_fit = payload.preferred_fit
  tp.updated_at = datetime.now(timezone.utc)
  db.commit()
  return taste_to_api(tp)
