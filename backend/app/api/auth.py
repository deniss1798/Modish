"""Регистрация и вход."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import business
from ..models import StyleProfile, User
from ..services.recommendation_engine import ensure_style_profile, ensure_taste_profile
from .deps import create_access_token, get_db, hash_password, verify_password
from .seed import seed_recommendations
from .serializers import as_user_payload

router = APIRouter(tags=["auth"])


class AuthRequest(BaseModel):
  email: EmailStr
  password: str = Field(min_length=8)


class TokenResponse(BaseModel):
  access_token: str
  token_type: str = "bearer"
  user: dict[str, Any]


@router.post("/auth/register", response_model=TokenResponse)
def register(payload: AuthRequest, db: Session = Depends(get_db)) -> TokenResponse:
  existing = db.execute(select(User).where(User.email == payload.email)).scalar_one_or_none()
  if existing:
    from fastapi import HTTPException

    raise HTTPException(status_code=409, detail="Email уже зарегистрирован")
  now = datetime.now(timezone.utc)
  user = User(
    id=str(uuid4()),
    email=payload.email,
    password_hash=hash_password(payload.password),
    trial_started_at=now,
    trial_ends_at=now + timedelta(days=14),
    plan="plus",
    subscription_status="trial",
    height_cm=170,
    weight_kg=None,
    fit_preference="regular",
  )
  db.add(user)
  db.flush()
  db.add(
    StyleProfile(
      id=str(uuid4()),
      user_id=user.id,
      style_target="unknown",
      confidence_score=0.5,
      profile_json={},
    )
  )
  db.commit()
  business.get_or_refresh_limits(db, user.id)
  db.commit()
  seed_recommendations(db, user.id)
  ensure_taste_profile(db, user.id)
  business.rebuild_user_summary(db, user.id)
  db.commit()
  return TokenResponse(access_token=create_access_token(user.id), user=as_user_payload(user))


@router.post("/auth/login", response_model=TokenResponse)
def login(payload: AuthRequest, db: Session = Depends(get_db)) -> TokenResponse:
  user = db.execute(select(User).where(User.email == payload.email)).scalar_one_or_none()
  if user is None or not verify_password(payload.password, user.password_hash):
    raise HTTPException(status_code=401, detail="Неверный email или пароль")
  business.get_or_refresh_limits(db, user.id)
  db.commit()
  seed_recommendations(db, user.id)
  ensure_style_profile(db, user.id)
  ensure_taste_profile(db, user.id)
  db.commit()
  return TokenResponse(access_token=create_access_token(user.id), user=as_user_payload(user))
