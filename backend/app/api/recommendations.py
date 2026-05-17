"""События по товарам и legacy-рекомендации (карточки образов)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .. import business
from ..models import (
  Recommendation,
  RecommendationEvent,
  RecommendationEventV2,
  StyleProfile,
  User,
  UserProductState,
)
from ..services.outfit_service import generate_outfits
from ..services.recommendation_engine import _product_category_norms, ensure_taste_profile
from .deps import auth_scheme, get_db, user_from_token
from .serializers import rec_to_dict

router = APIRouter(tags=["recommendations"])


class EventRequest(BaseModel):
  event_type: str = Field(
    pattern="^(view|skip|dislike|like|save|unsave|open_product|buy_click)$"
  )
  product_id: str | None = None
  outfit_id: str | None = None
  meta: dict[str, Any] | None = None


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
  from ..models import Outfit, Product

  user = user_from_token(credentials, db)
  # click/open/buy сильнее save/like (P6)
  weights = {
    "view": 0,
    "skip": -1,
    "dislike": -4,
    "like": 3,
    "save": 5,
    "unsave": -5,
    "open_product": 10,
    "buy_click": 15,
  }
  weight = weights[payload.event_type]
  if payload.product_id is None and payload.outfit_id is None:
    raise HTTPException(status_code=400, detail="product_id or outfit_id required")

  if payload.product_id:
    now = datetime.now(timezone.utc)
    st = db.execute(
      select(UserProductState).where(
        UserProductState.user_id == user.id,
        UserProductState.product_id == payload.product_id,
      )
    ).scalar_one_or_none()
    if st is None:
      st = UserProductState(
        id=str(uuid4()),
        user_id=user.id,
        product_id=payload.product_id,
        hidden_until=None,
        last_seen_at=None,
        event_strength=0,
        created_at=now,
        updated_at=now,
      )
      db.add(st)
      db.flush()
    st.last_seen_at = now if payload.event_type in ("view", "open_product") else st.last_seen_at
    st.event_strength = int(st.event_strength or 0) + int(weight or 0)

    if payload.event_type == "skip":
      st.hidden_until = now + timedelta(hours=24)
    elif payload.event_type == "dislike":
      st.hidden_until = now + timedelta(days=30)
    elif payload.event_type == "like":
      st.hidden_until = now + timedelta(hours=6)
    elif payload.event_type == "save":
      st.hidden_until = now + timedelta(days=3650)
    elif payload.event_type == "buy_click":
      st.hidden_until = now + timedelta(days=3650)
    elif payload.event_type == "unsave":
      st.hidden_until = now + timedelta(hours=6)
    st.updated_at = now

  if payload.product_id and payload.event_type in ("save", "unsave"):
    last = db.execute(
      select(RecommendationEventV2.event_type)
      .where(
        RecommendationEventV2.user_id == user.id,
        RecommendationEventV2.product_id == payload.product_id,
        RecommendationEventV2.event_type.in_(["save", "unsave"]),
      )
      .order_by(RecommendationEventV2.created_at.desc())
      .limit(1)
    ).scalar_one_or_none()
    if last == payload.event_type:
      return {
        "status": "ok",
        "event_type": payload.event_type,
        "weight": 0,
        "total_events": int(
          db.execute(
            select(func.count())
            .select_from(RecommendationEventV2)
            .where(RecommendationEventV2.user_id == user.id)
          ).scalar_one()
          or 0
        ),
        "milestone_reached": False,
        "outfits_generated": 0,
        "idempotent": True,
      }

  ev = RecommendationEventV2(
    id=str(uuid4()),
    user_id=user.id,
    product_id=payload.product_id,
    outfit_id=payload.outfit_id,
    event_type=payload.event_type,
    event_weight=weight,
    meta_json=payload.meta or {},
    created_at=datetime.now(timezone.utc),
  )
  db.add(ev)

  tp = ensure_taste_profile(db, user.id)
  if payload.product_id:
    p = db.execute(select(Product).where(Product.id == payload.product_id)).scalar_one_or_none()
    if p:
      cat_keys = _product_category_norms(p)
      cat = next(iter(cat_keys), "") or (p.category or "").strip().lower()
      brand = (p.brand or "").strip().lower()
      cols = [str(c).strip().lower() for c in (p.colors or []) if str(c).strip()]
      styles = [str(t).strip().lower() for t in (p.style_tags or []) if str(t).strip()]

      def add_unique(lst: list[str], v: str) -> None:
        if v and v not in lst:
          lst.append(v)

      def bump(d: dict, key: str, delta: int) -> None:
        if not key:
          return
        cur = d.get(key, 0)
        try:
          cur = int(cur)
        except Exception:
          cur = 0
        nxt = cur + int(delta)
        if nxt > 50:
          nxt = 50
        if nxt < -50:
          nxt = -50
        d[key] = nxt

      def prune(d: dict, limit: int = 200) -> None:
        if not isinstance(d, dict) or len(d) <= limit:
          return
        items: list[tuple[str, int]] = []
        for k, v in d.items():
          try:
            items.append((str(k), int(v)))
          except Exception:
            continue
        items.sort(key=lambda x: abs(x[1]), reverse=True)
        keep = {k for k, _ in items[:limit]}
        drop = [k for k in list(d.keys()) if str(k) not in keep]
        for k in drop:
          d.pop(k, None)

      if payload.event_type in ("like", "save", "open_product", "buy_click"):
        add_unique(tp.liked_categories, cat)
        add_unique(tp.liked_brands, brand)
        for c in cols[:3]:
          add_unique(tp.liked_colors, c)
        for t in styles[:3]:
          add_unique(tp.liked_styles, t)
      elif payload.event_type in ("dislike", "skip"):
        add_unique(tp.disliked_categories, cat)
        add_unique(tp.disliked_brands, brand)
        for c in cols[:3]:
          add_unique(tp.disliked_colors, c)
        for t in styles[:3]:
          add_unique(tp.disliked_styles, t)

      if payload.event_type == "view":
        for ck in cat_keys:
          bump(tp.category_weights, ck, +1)
        if brand:
          bump(tp.brand_weights, brand, +1)
        for c in cols[:2]:
          bump(tp.color_weights, c, +1)

      delta = weight
      bump(tp.category_weights, cat, delta)
      bump(tp.brand_weights, brand, delta)
      for c in cols[:3]:
        bump(tp.color_weights, c, delta)
      for t in styles[:3]:
        bump(tp.style_weights, t, delta)
      prune(tp.category_weights)
      prune(tp.brand_weights)
      prune(tp.color_weights)
      prune(tp.style_weights)
      tp.updated_at = datetime.now(timezone.utc)

  db.commit()
  total = db.execute(
    select(func.count()).select_from(RecommendationEventV2).where(RecommendationEventV2.user_id == user.id)
  ).scalar_one()
  total = int(total or 0)
  milestone = total in (20, 30)
  generated = 0
  if milestone:
    existing = db.execute(select(Outfit).where(Outfit.user_id == user.id)).scalars().first()
    if existing is None:
      items = generate_outfits(db, user, count=3)
      db.commit()
      generated = len(items)
  return {
    "status": "ok",
    "event_type": payload.event_type,
    "weight": weight,
    "total_events": total,
    "milestone_reached": milestone,
    "outfits_generated": generated,
  }


@router.post("/recommendations/generate")
def recommendations_generate(
  payload: GenerateRequest,
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> list[dict[str, Any]]:
  user = user_from_token(credentials, db)
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
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> list[dict[str, Any]]:
  """Персонализированная товарная лента (alias для GET /feed)."""
  from .products import _feed_scored_items

  user = user_from_token(credentials, db)
  return _feed_scored_items(db, user, limit=limit, source=source)


@router.get("/recommendations/outfits-legacy")
def recommendations_outfits_legacy(
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> list[dict[str, Any]]:
  user = user_from_token(credentials, db)
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
  db.commit()
  total_events = db.execute(
    select(func.count()).select_from(RecommendationEvent).where(RecommendationEvent.user_id == user.id)
  ).scalar_one()
  total_events = int(total_events or 0)
  if total_events > 0 and total_events % 10 == 0:
    business.rebuild_user_summary(db, user.id)
    db.commit()
  return {"status": "ok", "event_type": payload.event_type}
