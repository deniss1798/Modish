from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import bcrypt
import jwt
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from . import business
from .config import get_jwt_expires_hours, get_jwt_secret
from .db import SessionLocal
from .schemas.photo_analysis import build_profile_json_after_analysis, mock_photo_analysis_v1
from .models import (
  Recommendation,
  RecommendationEvent,
  SavedRecommendation,
  StyleProfile,
  User,
)

app = FastAPI(title="Modish API", version="0.9.0-pre")
auth_scheme = HTTPBearer(auto_error=False)
JWT_SECRET = get_jwt_secret()
JWT_ALG = "HS256"
JWT_EXPIRES_HOURS = get_jwt_expires_hours()

MAX_PHOTO_BYTES = business.MAX_PHOTO_BYTES


class AuthRequest(BaseModel):
  email: EmailStr
  password: str = Field(min_length=8)


class StyleTargetRequest(BaseModel):
  style_target: str = Field(pattern="^(menswear|womenswear|unisex|unknown)$")


class FeedbackRequest(BaseModel):
  event_type: str = Field(pattern="^(like|dislike|save|unsave|view_details)$")


class TokenResponse(BaseModel):
  access_token: str
  token_type: str = "bearer"
  user: dict[str, Any]


class GenerateRequest(BaseModel):
  type: str = Field(default="outfit", pattern="^(outfit)$")
  count: int = Field(default=10, ge=1, le=10)
  scenario: str = Field(default="daily", pattern="^(daily|office|evening)$")


class StyleProfilePatchRequest(BaseModel):
  profile_json: dict[str, Any] | None = None
  style_target: str | None = None


def get_db():
  db = SessionLocal()
  try:
    yield db
  finally:
    db.close()


def _hash(password: str) -> str:
  raw = password.encode("utf-8")
  if len(raw) > 72:
    raise HTTPException(status_code=400, detail="Пароль слишком длинный (bcrypt: макс. 72 байта)")
  return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("ascii")


def _verify_password(plain: str, hashed: str) -> bool:
  try:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("ascii"))
  except (ValueError, TypeError):
    return False


def _token(user_id: str) -> str:
  now = datetime.now(timezone.utc)
  payload = {
    "sub": user_id,
    "iat": int(now.timestamp()),
    "exp": int((now + timedelta(hours=JWT_EXPIRES_HOURS)).timestamp()),
  }
  return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def _user_from_token(
  credentials: HTTPAuthorizationCredentials | None,
  db: Session,
) -> User:
  if credentials is None or credentials.scheme.lower() != "bearer":
    raise HTTPException(
      status_code=status.HTTP_401_UNAUTHORIZED,
      detail="Missing bearer token",
    )
  try:
    payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALG])
  except jwt.PyJWTError as exc:
    raise HTTPException(
      status_code=status.HTTP_401_UNAUTHORIZED,
      detail="Invalid token",
    ) from exc
  user_id = payload.get("sub")
  if not user_id:
    raise HTTPException(
      status_code=status.HTTP_401_UNAUTHORIZED,
      detail="Invalid token payload",
    )
  user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
  if user is None:
    raise HTTPException(
      status_code=status.HTTP_401_UNAUTHORIZED,
      detail="User not found",
    )
  return user


def _as_user_payload(user: User) -> dict[str, Any]:
  return {
    "id": user.id,
    "email": user.email,
    "plan": user.plan,
    "subscription_status": user.subscription_status,
    "trial_started_at": user.trial_started_at.isoformat(),
    "trial_ends_at": user.trial_ends_at.isoformat(),
  }


def _rec_to_dict(item: Recommendation) -> dict[str, Any]:
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


def _seed_recommendations(db: Session, user_id: str) -> None:
  existing = db.execute(
    select(Recommendation).where(Recommendation.user_id == user_id)
  ).scalars().first()
  if existing:
    return
  base = Recommendation(
    id=str(uuid4()),
    user_id=user_id,
    type="outfit",
    title="Образ на каждый день",
    description="Спокойный минималистичный образ для повседневного использования.",
    content_json={
      "items": {
        "top": "белый лонгслив",
        "bottom": "прямые тёмные джинсы",
        "layer": "серый жакет",
        "shoes": "белые кроссовки",
      },
      "why_it_fits": ["чистый силуэт", "легко повторить", "спокойный контраст"],
      "alternatives": [
        "лонгслив можно заменить на белую футболку",
        "кроссовки можно заменить на лоферы",
      ],
    },
    tags_json={
      "styles": ["minimal", "smart_casual"],
      "colors": ["white", "navy", "gray"],
      "silhouettes": ["straight", "structured"],
      "occasion": ["daily"],
      "item_types": ["longsleeve", "jeans", "jacket", "sneakers"],
    },
    status="active",
  )
  db.add(base)
  db.commit()


async def _read_upload_limited(photo: UploadFile, max_bytes: int) -> None:
  total = 0
  while True:
    chunk = await photo.read(65536)
    if not chunk:
      break
    total += len(chunk)
    if total > max_bytes:
      raise HTTPException(status_code=400, detail="Файл больше 5 MB")


@app.get("/health")
def health() -> dict[str, str]:
  return {"status": "ok"}


@app.post("/auth/register", response_model=TokenResponse)
def register(payload: AuthRequest, db: Session = Depends(get_db)) -> TokenResponse:
  existing = db.execute(select(User).where(User.email == payload.email)).scalar_one_or_none()
  if existing:
    raise HTTPException(status_code=409, detail="Email уже зарегистрирован")
  now = datetime.now(timezone.utc)
  user = User(
    id=str(uuid4()),
    email=payload.email,
    password_hash=_hash(payload.password),
    trial_started_at=now,
    trial_ends_at=now + timedelta(days=14),
    plan="plus",
    subscription_status="trial",
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
  _seed_recommendations(db, user.id)
  business.rebuild_user_summary(db, user.id)
  db.commit()
  return TokenResponse(access_token=_token(user.id), user=_as_user_payload(user))


@app.post("/auth/login", response_model=TokenResponse)
def login(payload: AuthRequest, db: Session = Depends(get_db)) -> TokenResponse:
  user = db.execute(select(User).where(User.email == payload.email)).scalar_one_or_none()
  if user is None or not _verify_password(payload.password, user.password_hash):
    raise HTTPException(status_code=401, detail="Неверный email или пароль")
  business.get_or_refresh_limits(db, user.id)
  db.commit()
  _seed_recommendations(db, user.id)
  return TokenResponse(access_token=_token(user.id), user=_as_user_payload(user))


@app.get("/users/me")
def users_me(
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, Any]:
  user = _user_from_token(credentials, db)
  return _as_user_payload(user)


@app.get("/billing/status")
def billing_status(
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, Any]:
  user = _user_from_token(credentials, db)
  return business.billing_status_payload(user)


@app.get("/style-profile/me")
def style_profile_me(
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, Any]:
  user = _user_from_token(credentials, db)
  profile = db.execute(select(StyleProfile).where(StyleProfile.user_id == user.id)).scalar_one()
  return {
    "id": profile.id,
    "user_id": profile.user_id,
    "style_target": profile.style_target,
    "confidence_score": profile.confidence_score,
    "profile_json": profile.profile_json,
    "updated_at": profile.updated_at.isoformat(),
  }


@app.patch("/style-profile/me")
def style_profile_patch(
  payload: StyleProfilePatchRequest,
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, Any]:
  user = _user_from_token(credentials, db)
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


@app.patch("/style-profile/target")
def patch_style_target(
  payload: StyleTargetRequest,
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, Any]:
  user = _user_from_token(credentials, db)
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


@app.post("/style-profile/analyze")
async def analyze(
  photo: UploadFile = File(...),
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, Any]:
  ct = (photo.content_type or "").lower()
  if ct not in {"image/jpeg", "image/jpg", "image/png", "image/webp"}:
    raise HTTPException(status_code=400, detail="Допустимы jpg, jpeg, png, webp")
  user = _user_from_token(credentials, db)
  try:
    limits = business.assert_can_analyze(db, user)
  except PermissionError as e:
    raise HTTPException(status_code=403, detail=str(e)) from e
  await _read_upload_limited(photo, MAX_PHOTO_BYTES)
  profile = db.execute(select(StyleProfile).where(StyleProfile.user_id == user.id)).scalar_one()
  analysis = mock_photo_analysis_v1()
  profile.profile_json = build_profile_json_after_analysis(
    analysis,
    source="photo_analysis_v1_mock",
  )
  profile.confidence_score = analysis.confidence_score
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


@app.post("/recommendations/generate")
def recommendations_generate(
  payload: GenerateRequest,
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> list[dict[str, Any]]:
  user = _user_from_token(credentials, db)
  try:
    limits = business.assert_can_generate(db, user)
  except PermissionError as e:
    raise HTTPException(status_code=403, detail=str(e)) from e
  items = business.generate_outfit_recommendations(db, user.id, payload.count, payload.scenario)
  limits.recommendation_batches_used += 1
  limits.updated_at = datetime.now(timezone.utc)
  db.commit()
  return [_rec_to_dict(r) for r in items]


@app.get("/recommendations/feed")
def recommendations_feed(
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> list[dict[str, Any]]:
  user = _user_from_token(credentials, db)
  reviewed = set(
    db.execute(
      select(RecommendationEvent.recommendation_id).where(
        RecommendationEvent.user_id == user.id,
        RecommendationEvent.event_type.in_(["like", "dislike"]),
      )
    ).scalars()
  )
  items = db.execute(select(Recommendation).where(Recommendation.user_id == user.id)).scalars().all()
  return [_rec_to_dict(item) for item in items if item.id not in reviewed]


@app.get("/recommendations/saved")
def recommendations_saved(
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> list[dict[str, Any]]:
  user = _user_from_token(credentials, db)
  rows = db.execute(
    select(SavedRecommendation, Recommendation)
    .join(Recommendation, Recommendation.id == SavedRecommendation.recommendation_id)
    .where(SavedRecommendation.user_id == user.id)
    .order_by(SavedRecommendation.created_at.desc())
  ).all()
  out: list[dict[str, Any]] = []
  for saved, rec in rows:
    out.append(
      {
        "id": saved.id,
        "recommendation_id": rec.id,
        "saved_at": saved.created_at.isoformat(),
        "recommendation": _rec_to_dict(rec),
      }
    )
  return out


@app.get("/recommendations/summary")
def recommendations_summary(
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, Any]:
  user = _user_from_token(credentials, db)
  profile = db.execute(select(StyleProfile).where(StyleProfile.user_id == user.id)).scalar_one()
  summary = business.ensure_summary_row(db, user.id)
  db.commit()
  return business.summary_to_api(summary, profile)


@app.post("/recommendations/summary/rebuild")
def recommendations_summary_rebuild(
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, Any]:
  user = _user_from_token(credentials, db)
  profile = db.execute(select(StyleProfile).where(StyleProfile.user_id == user.id)).scalar_one()
  summary = business.rebuild_user_summary(db, user.id)
  db.commit()
  return business.summary_to_api(summary, profile)


@app.get("/recommendations/{recommendation_id}")
def recommendation_detail(
  recommendation_id: str,
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, Any]:
  user = _user_from_token(credentials, db)
  rec = db.execute(
    select(Recommendation).where(
      Recommendation.id == recommendation_id,
      Recommendation.user_id == user.id,
    )
  ).scalar_one_or_none()
  if rec is None:
    raise HTTPException(status_code=404, detail="Карточка не найдена")
  return _rec_to_dict(rec)


@app.post("/recommendations/{recommendation_id}/feedback")
def recommendations_feedback(
  recommendation_id: str,
  payload: FeedbackRequest,
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, Any]:
  user = _user_from_token(credentials, db)
  recommendation = db.execute(
    select(Recommendation).where(Recommendation.id == recommendation_id)
  ).scalar_one_or_none()
  if recommendation is None or recommendation.user_id != user.id:
    raise HTTPException(status_code=404, detail="Карточка не найдена")
  weight = {"like": 1, "dislike": -2, "save": 3, "unsave": -1, "view_details": 0}[payload.event_type]
  tags = recommendation.tags_json or {}
  db.add(
    RecommendationEvent(
      id=str(uuid4()),
      user_id=user.id,
      recommendation_id=recommendation_id,
      event_type=payload.event_type,
      event_weight=weight,
    )
  )
  business.apply_preferences_from_tags(db, user.id, tags, weight)
  if payload.event_type == "save":
    exists = db.execute(
      select(SavedRecommendation).where(
        SavedRecommendation.user_id == user.id,
        SavedRecommendation.recommendation_id == recommendation_id,
      )
    ).scalar_one_or_none()
    if exists is None:
      db.add(
        SavedRecommendation(
          id=str(uuid4()),
          user_id=user.id,
          recommendation_id=recommendation_id,
        )
      )
  elif payload.event_type == "unsave":
    db.execute(
      delete(SavedRecommendation).where(
        SavedRecommendation.user_id == user.id,
        SavedRecommendation.recommendation_id == recommendation_id,
      )
    )
  db.commit()
  total_events = db.execute(
    select(func.count()).select_from(RecommendationEvent).where(RecommendationEvent.user_id == user.id)
  ).scalar_one()
  total_events = int(total_events or 0)
  if total_events > 0 and total_events % 10 == 0:
    business.rebuild_user_summary(db, user.id)
    db.commit()
  return {"status": "ok", "event_type": payload.event_type}
