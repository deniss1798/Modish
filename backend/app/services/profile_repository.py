"""Shared profile persistence; independent of recommendation/API orchestration."""
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import FitProfile, StyleProfile, TasteProfile, User
from .recommendation_revision import owner_mutation, RecommendationSnapshotChanged


def ensure_style_profile(db: Session, user_id: str) -> StyleProfile:
  row = db.execute(select(StyleProfile).where(StyleProfile.user_id == user_id)).scalar_one_or_none()
  if row:
    return row
  if db.info.get('recommendation_read_only'):
    raise RecommendationSnapshotChanged()
  return _create_style_profile(db, user_id)


def _create_style_profile(db: Session, user_id: str) -> StyleProfile:
  with owner_mutation(db, user_id) as mutation:
    row = db.execute(select(StyleProfile).where(StyleProfile.user_id == user_id)).scalar_one_or_none()
    if row:
      return row
    now = datetime.now(timezone.utc)
    row = StyleProfile(
      id=str(uuid4()),
      user_id=user_id,
      style_target="unknown",
      confidence_score=0.5,
      profile_json={},
      created_at=now,
      updated_at=now,
    )
    db.add(row)
    db.flush()
    mutation.mark_changed()
    return row


def ensure_taste_profile(db: Session, user_id: str, *, as_of: datetime | None = None) -> TasteProfile:
  row = db.execute(select(TasteProfile).where(TasteProfile.user_id == user_id)).scalar_one_or_none()
  if row:
    return row
  if db.info.get('recommendation_read_only'):
    raise RecommendationSnapshotChanged()
  return _create_taste_profile(db, user_id, as_of=as_of)


def _create_taste_profile(db: Session, user_id: str, *, as_of: datetime | None = None) -> TasteProfile:
  with owner_mutation(db, user_id) as mutation:
    row = db.execute(select(TasteProfile).where(TasteProfile.user_id == user_id)).scalar_one_or_none()
    if row:
      return row
    now = as_of or datetime.now(timezone.utc)
    row = TasteProfile(
      id=str(uuid4()),
      user_id=user_id,
      liked_categories=[],
      disliked_categories=[],
      liked_colors=[],
      disliked_colors=[],
      liked_brands=[],
      disliked_brands=[],
      liked_styles=[],
      disliked_styles=[],
      category_weights={},
      brand_weights={},
      color_weights={},
      style_weights={},
      price_min=0,
      price_max=10_000,
      preferred_fit="regular",
      profile_version=1,
      created_at=now,
      updated_at=now,
    )
    db.add(row)
    db.flush()
    mutation.mark_changed()
    return row


def load_preferences(db: Session, user: User) -> tuple[StyleProfile, FitProfile | None, TasteProfile]:
    profile = ensure_style_profile(db, user.id)
    fit = db.scalar(select(FitProfile).where(FitProfile.user_id == user.id))
    taste = ensure_taste_profile(db, user.id)
    return profile, fit, taste
