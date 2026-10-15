"""События по товарам и legacy-рекомендации (карточки образов)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session
from ..services.recommendation_revision import owner_mutation

from .. import business
from ..models import (
  FitProfile,
  Recommendation,
  RecommendationEvent,
  StyleProfile,
)
# Re-export these names for existing internal consumers during migration.
from ..schemas.product_event import EventRequest
from ..services.product_event_service import record_product_event
from .deps import auth_scheme, get_db, user_from_token
from .serializers import rec_to_dict

router = APIRouter(tags=["recommendations"])


def _has_gender_choice(db, user):
  choice = db.scalar(select(FitProfile.gender_target).where(FitProfile.user_id == user.id))
  return choice in {'menswear', 'womenswear', 'unisex'}


class GenerateRequest(BaseModel):
  type: str = Field(default="outfit", pattern="^(outfit)$")
  count: int = Field(default=10, ge=1, le=10)
  scenario: str = Field(default="daily", pattern="^(daily|office|evening)$")


class FeedbackRequest(BaseModel):
  event_type: str = Field(pattern="^(like|dislike|save|unsave|view_details)$")


@router.post("/recommendations/events")
def recommendations_events(
  payload: EventRequest,
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, Any]:
  user = user_from_token(credentials, db)
  return record_product_event(db, user, payload)


@router.post("/recommendations/generate")
def recommendations_generate(
  payload: GenerateRequest,
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> list[dict[str, Any]]:
  user = user_from_token(credentials, db)
  if not _has_gender_choice(db, user):
    raise HTTPException(status_code=409, detail='Явно выберите гардероб в профиле.')
  try:
    limits = business.assert_can_generate(db, user)
  except PermissionError as e:
    raise HTTPException(status_code=403, detail=str(e)) from e
  items = business.generate_outfit_recommendations(db, user.id, payload.count, payload.scenario)
  limits.recommendation_batches_used += 1
  limits.updated_at = datetime.now(timezone.utc)
  db.commit()
  return [rec_to_dict(r) for r in items]


@router.get("/recommendations/feed")
def recommendations_feed(
  limit: int = 30,
  source: str | None = None,
  scenario: str = "daily",
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> list[dict[str, Any]]:
  """Персонализированная товарная лента (alias для GET /feed)."""
  from .products import _feed_scored_items

  user = user_from_token(credentials, db)
  return _feed_scored_items(db, user, limit=limit, source=source, scenario=scenario)


@router.get("/recommendations/outfits-legacy")
def recommendations_outfits_legacy(
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> list[dict[str, Any]]:
  user = user_from_token(credentials, db)
  if not _has_gender_choice(db, user):
    return []
  reviewed = set(
    db.execute(
      select(RecommendationEvent.recommendation_id).where(
        RecommendationEvent.user_id == user.id,
        RecommendationEvent.event_type.in_(["like", "dislike"]),
      )
    ).scalars()
  )
  items = db.execute(select(Recommendation).where(Recommendation.user_id == user.id)).scalars().all()
  return [rec_to_dict(item) for item in items if item.id not in reviewed]


@router.get("/recommendations/saved")
def recommendations_saved(
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> list[dict[str, Any]]:
  from ..models import SavedRecommendation

  user = user_from_token(credentials, db)
  if not _has_gender_choice(db, user):
    return []
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
        "recommendation": rec_to_dict(rec),
      }
    )
  return out


@router.get("/recommendations/summary")
def recommendations_summary(
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, Any]:
  user = user_from_token(credentials, db)
  profile = db.execute(select(StyleProfile).where(StyleProfile.user_id == user.id)).scalar_one()
  summary = business.ensure_summary_row(db, user.id)
  db.commit()
  return business.summary_to_api(summary, profile)


@router.post("/recommendations/summary/rebuild")
def recommendations_summary_rebuild(
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, Any]:
  user = user_from_token(credentials, db)
  profile = db.execute(select(StyleProfile).where(StyleProfile.user_id == user.id)).scalar_one()
  summary = business.rebuild_user_summary(db, user.id)
  db.commit()
  return business.summary_to_api(summary, profile)


@router.get("/recommendations/{recommendation_id}")
def recommendation_detail(
  recommendation_id: str,
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, Any]:
  user = user_from_token(credentials, db)
  if not _has_gender_choice(db, user):
    raise HTTPException(status_code=404, detail="Карточка не найдена")
  rec = db.execute(
    select(Recommendation).where(
      Recommendation.id == recommendation_id,
      Recommendation.user_id == user.id,
    )
  ).scalar_one_or_none()
  if rec is None:
    raise HTTPException(status_code=404, detail="Карточка не найдена")
  return rec_to_dict(rec)


@router.post("/recommendations/{recommendation_id}/feedback")
def recommendations_feedback(
  recommendation_id: str,
  payload: FeedbackRequest,
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, Any]:
  from ..models import SavedRecommendation

  user = user_from_token(credentials, db)
  with owner_mutation(db, user.id) as mutation:
    recommendation = db.execute(
      select(Recommendation).where(Recommendation.id == recommendation_id)
    ).scalar_one_or_none()
    if recommendation is None or recommendation.user_id != user.id:
      raise HTTPException(status_code=404, detail="Карточка не найдена")
    weight = {"like": 2, "dislike": -2, "save": 3, "unsave": -1, "view_details": 0}[payload.event_type]
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
    mutation.mark_changed()
  db.commit()
  total_events = db.execute(
    select(func.count()).select_from(RecommendationEvent).where(RecommendationEvent.user_id == user.id)
  ).scalar_one()
  total_events = int(total_events or 0)
  if total_events > 0 and total_events % 10 == 0:
    business.rebuild_user_summary(db, user.id)
    db.commit()
  return {"status": "ok", "event_type": payload.event_type}
