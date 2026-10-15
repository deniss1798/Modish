"""Product feedback application service shared by the API and onboarding.

The user row lock serializes taste/state writes across workers. With commit=False,
the caller owns the transaction (including rollback); no events are committed here.
"""
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import RecommendationEventV2, User, UserProductState
from ..money import json_rubles
from ..schemas.product_event import EventRequest
from .outfit_service import generate_outfits
from .profile_repository import ensure_taste_profile
from .recommendation_config import (
    HIDE_FOR, LAST_SEEN_EVENTS, NEGATIVE_LIST_EVENTS, POSITIVE_LIST_EVENTS,
    TASTE_UPDATE_EVENTS, event_weight, feature_targets_for_event,
    legacy_feature_signal_multiplier, normalize_event_type,
    normalized_event_meta, recommendation_algorithm_metadata,
)
from .recommendation_engine import _product_category_norms
from .user_twin_service import apply_event_to_user_twin
from .recommendation_revision import accept_personal_change


def record_product_event(db: Session, user: User, payload: EventRequest, *, commit: bool = True) -> dict[str, Any]:
  from ..models import Outfit, Product

  db.execute(select(User.id).where(User.id == user.id).with_for_update()).scalar_one()
  if payload.product_id and db.get(Product, payload.product_id) is None:
    raise HTTPException(status_code=404, detail="Товар не найден")
  try:
    original_event_type = (payload.event_type or "").strip().lower()
    canonical_event_type = normalize_event_type(original_event_type)
    meta = normalized_event_meta(
      event_type=canonical_event_type,
      meta=payload.meta,
      original_event_type=original_event_type,
    )
    for key, value in recommendation_algorithm_metadata(
      scenario=str(meta.get("scenario") or "") or None
    ).items():
      meta.setdefault(key, value)
  except ValueError as exc:
    raise HTTPException(status_code=400, detail=str(exc)) from exc
  event_outfit_id = payload.outfit_id
  task_intent = None
  if meta.get('surface') == 'shopping_intent':
    from .shopping_intent_service import get_intent, IntentError
    try:
      intent = get_intent(db, user, str(meta.get('intent_id') or ''))
    except IntentError as error:
      raise HTTPException(status_code=error.status, detail=str(error)) from error
    owned = set((intent.context_json or {}).get('product_ids', []))
    owned.update(pid for o in (intent.context_json or {}).get('outfits', []) for pid in o['items'].values())
    if meta.get('intent_version') != intent.version:
      raise HTTPException(status_code=409, detail='Версия запроса уже изменилась')
    if payload.product_id not in owned:
      raise HTTPException(status_code=404, detail='Товар не был выдан в этом запросе')
    task_intent = intent
    attributed = meta.get('intent_outfit_id')
    if event_outfit_id and attributed and event_outfit_id != attributed:
      raise HTTPException(status_code=400, detail='Несогласованный образ события')
    event_outfit_id = event_outfit_id or attributed
    if event_outfit_id:
      selected = next((o for o in (intent.context_json or {}).get('outfits', [])
          if o['id'] == event_outfit_id), None)
      if selected is None or payload.product_id not in selected['items'].values():
        raise HTTPException(status_code=404, detail='Вещь не была выдана в этом образе')
      meta['intent_outfit_id'] = event_outfit_id
    if canonical_event_type == 'view':
      from .intent_metrics import emit
      emit(db, user.id, 'product_viewed', intent_id=intent.id, version=intent.version, product_id=payload.product_id)
      emit(db, user.id, 'recommendation_explained', intent_id=intent.id, version=intent.version,
          product_id=payload.product_id, outfit_id=event_outfit_id)
  if payload.product_id:
    observed_product = db.get(Product, payload.product_id)
    from ..catalog_normalize import normalize_category
    meta.update(observed_price=json_rubles(observed_product.price) if observed_product.currency in {'RUB', 'RUR'} else 0,
        observed_brand=str(observed_product.brand or ''),
        observed_category=normalize_category(observed_product.category))
  weight = event_weight(canonical_event_type)
  if payload.product_id is None and payload.outfit_id is None:
    raise HTTPException(status_code=400, detail="product_id or outfit_id required")

  if payload.product_id and canonical_event_type in ("save", "unsave"):
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
    if last == canonical_event_type:
      return {
        "status": "ok",
        "event_type": canonical_event_type,
        "weight": 0.0,
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
        event_strength=0.0,
        created_at=now,
        updated_at=now,
      )
      db.add(st)
      db.flush()
    st.last_seen_at = now if canonical_event_type in LAST_SEEN_EVENTS else st.last_seen_at
    st.event_strength = float(st.event_strength or 0.0) + float(weight or 0.0)

    hide_for = HIDE_FOR.get(canonical_event_type)
    if hide_for is not None:
      st.hidden_until = now + hide_for
    st.updated_at = now

  ev = RecommendationEventV2(
    id=str(uuid4()),
    user_id=user.id,
    product_id=payload.product_id,
    outfit_id=event_outfit_id,
    event_type=canonical_event_type,
    event_weight=weight,
    meta_json=meta,
    created_at=datetime.now(timezone.utc),
  )
  db.add(ev)
  from .intent_metrics import emit
  metric_context = {'intent_id': task_intent.id, 'version': task_intent.version} if task_intent else {}
  if canonical_event_type in {'dislike', 'post_purchase_negative'} and meta.get('reason'):
    emit(db, user.id, 'reason_dislike', **metric_context, product_id=payload.product_id, reason=meta['reason'])
  # This read-derived profile consumes the accepted behavioral evidence. The
  # event denotes new input, not a claim that a numerical score changed.
  from .behavior_profile import EXPOSURE_EVENTS
  if payload.product_id and observed_product.brand and canonical_event_type in EXPOSURE_EVENTS:
    emit(db, user.id, 'brand_affinity_updated', **metric_context, action=canonical_event_type)


  tp = ensure_taste_profile(db, user.id)
  if payload.product_id:
    p = db.execute(select(Product).where(Product.id == payload.product_id)).scalar_one_or_none()
    if p and not (canonical_event_type == 'view' and meta.get('surface') == 'shopping_intent'):
      cat_keys = _product_category_norms(p)
      cat = next(iter(cat_keys), "") or (p.category or "").strip().lower()
      brand = (p.brand or "").strip().lower()
      from ..catalog_normalize import normalize_colors_value
      cols = normalize_colors_value(p.colors or p.color_original)
      from .catalog.style_tagger import infer_style_tags
      styles = [str(t).strip().lower() for t in (p.style_tags or infer_style_tags(title=p.title, category=p.category)) if str(t).strip()]
      liked_categories = list(tp.liked_categories or [])
      liked_brands = list(tp.liked_brands or [])
      liked_colors = list(tp.liked_colors or [])
      liked_styles = list(tp.liked_styles or [])
      disliked_categories = list(tp.disliked_categories or [])
      disliked_brands = list(tp.disliked_brands or [])
      disliked_colors = list(tp.disliked_colors or [])
      disliked_styles = list(tp.disliked_styles or [])
      category_weights = dict(tp.category_weights or {})
      brand_weights = dict(tp.brand_weights or {})
      color_weights = dict(tp.color_weights or {})
      style_weights = dict(tp.style_weights or {})

      def add_unique(lst: list[str], v: str) -> None:
        if v and v not in lst:
          lst.append(v)

      def bump(d: dict, key: str, delta: float) -> None:
        if not key:
          return
        cur = d.get(key, 0)
        try:
          cur = float(cur)
        except Exception:
          cur = 0.0
        nxt = cur + float(delta)
        if nxt > 50:
          nxt = 50.0
        if nxt < -50:
          nxt = -50.0
        d[key] = nxt

      def prune(d: dict, limit: int = 200) -> None:
        if not isinstance(d, dict) or len(d) <= limit:
          return
        items: list[tuple[str, float]] = []
        for k, v in d.items():
          try:
            items.append((str(k), float(v)))
          except Exception:
            continue
        items.sort(key=lambda x: abs(x[1]), reverse=True)
        keep = {k for k, _ in items[:limit]}
        drop = [k for k in list(d.keys()) if str(k) not in keep]
        for k in drop:
          d.pop(k, None)

      if canonical_event_type in POSITIVE_LIST_EVENTS or (meta.get("surface") == "style_swipe" and canonical_event_type == "like"):
        add_unique(liked_categories, cat)
        add_unique(liked_brands, brand)
        for c in cols[:3]:
          add_unique(liked_colors, c)
        for t in styles[:3]:
          add_unique(liked_styles, t)
      elif canonical_event_type in NEGATIVE_LIST_EVENTS:
        add_unique(disliked_categories, cat)
        add_unique(disliked_brands, brand)
        for c in cols[:3]:
          add_unique(disliked_colors, c)
        for t in styles[:3]:
          add_unique(disliked_styles, t)

      if canonical_event_type in TASTE_UPDATE_EVENTS:
        delta = weight * legacy_feature_signal_multiplier(canonical_event_type, meta)
        targets = feature_targets_for_event(canonical_event_type, meta)
        if "category" in targets:
          for ck in (cat_keys or {cat}):
            bump(category_weights, ck, delta)
        if "brand" in targets:
          bump(brand_weights, brand, delta)
        if "color" in targets:
          for c in cols[:3]:
            bump(color_weights, c, delta)
        if "style" in targets:
          for t in styles[:3]:
            bump(style_weights, t, delta)
        prune(category_weights)
        prune(brand_weights)
        prune(color_weights)
        prune(style_weights)

      tp.liked_categories = liked_categories
      tp.liked_brands = liked_brands
      tp.liked_colors = liked_colors
      tp.liked_styles = liked_styles
      tp.disliked_categories = disliked_categories
      tp.disliked_brands = disliked_brands
      tp.disliked_colors = disliked_colors
      tp.disliked_styles = disliked_styles
      tp.category_weights = category_weights
      tp.brand_weights = brand_weights
      tp.color_weights = color_weights
      tp.style_weights = style_weights
      tp.updated_at = datetime.now(timezone.utc)
      apply_event_to_user_twin(
        db,
        user_id=user.id,
        product=p,
        event_type=canonical_event_type,
        meta=meta,
        occurred_at=now,
      )

  from .activation_service import record_exposure_event
  if meta.get("surface") != "style_swipe" and payload.product_id:
    record_exposure_event(db, user, payload.product_id, canonical_event_type, surface=meta.get('surface'))
  db.flush()
  accept_personal_change(db, user.id)
  if not commit:
    return {"status": "ok", "event_type": canonical_event_type}
  db.commit()
  total = db.execute(
    select(func.count()).select_from(RecommendationEventV2).where(RecommendationEventV2.user_id == user.id)
  ).scalar_one()
  total = int(total or 0)
  from ..models import ActivationSession
  milestone = total in (20, 30) and db.get(ActivationSession, user.id) is None
  generated = 0
  if milestone:
    existing = db.execute(select(Outfit).where(Outfit.user_id == user.id)).scalars().first()
    if existing is None:
      items = generate_outfits(db, user, count=3)
      db.commit()
      generated = len(items)
  return {
    "status": "ok",
    "event_type": canonical_event_type,
    "weight": weight,
    "total_events": total,
    "milestone_reached": milestone,
    "outfits_generated": generated,
  }
