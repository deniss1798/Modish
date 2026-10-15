"""Product feedback application service shared by the API and onboarding.

The user row lock serializes taste/state writes across workers. With commit=False,
the caller owns the transaction (including rollback); no events are committed here.
"""
from datetime import datetime, timezone
import logging
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from ..models import RecommendationEventV2, User, UserProductState
from ..money import json_rubles
from ..schemas.product_event import EventRequest
from .profile_repository import ensure_taste_profile
from .recommendation_config import (
    HIDE_FOR, LAST_SEEN_EVENTS, event_weight, normalize_event_type,
    normalized_event_meta, recommendation_algorithm_metadata,
)
from .legacy_taste_reducer import legacy_product_facts, reduce_legacy_taste
from .user_twin_service import apply_event_to_user_twin
from .recommendation_revision import accept_personal_change, owner_mutation

logger = logging.getLogger(__name__)


def record_product_event(db: Session, user: User, payload: EventRequest, *, commit: bool = True,
                         occurred_at: datetime | None = None) -> dict[str, Any]:
  # Refresh cached owner rows BEFORE reading legacy taste/state. A late revision
  # bump alone cannot repair a signal overwritten after waiting for the lock.
  with owner_mutation(db, user.id):
    result = _apply_product_event(db, user, payload, occurred_at=occurred_at)
  if result.get('idempotent'):
    return result
  if not commit:
    return {'status': result['status'], 'event_type': result['event_type']}
  db.commit()
  total, milestone, generated = None, False, 0
  try:
    total = db.scalar(select(func.count()).select_from(RecommendationEventV2).where(
      RecommendationEventV2.user_id == user.id)) or 0
    from ..models import ActivationSession
    milestone = total in (20, 30) and db.get(ActivationSession, user.id) is None
    if milestone:
      # The reaction is already durable. Release its connection before the
      # optional catalogue computation, including the read used for the count.
      from .outfit_session import build_outfits_response
      user_id, bind = user.id, db.get_bind()
      db.close()
      items = build_outfits_response(bind, user_id, count=3, only_if_empty=True)
      generated = len(items)
  except Exception:
    # Even the optional count may fail after the event committed. Preserve the
    # successful reaction; an unknown count is not a fabricated zero.
    db.rollback()
    logger.exception('Optional milestone outfit generation failed')
  return {**result, 'total_events': int(total) if total is not None else None, 'milestone_reached': milestone,
    'outfits_generated': generated}


def _apply_product_event(db: Session, user: User, payload: EventRequest, *, occurred_at=None) -> dict[str, Any]:
  """Commit-free application; caller holds and refreshes the owner first."""
  from ..models import Product

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
    now = occurred_at or datetime.now(timezone.utc)
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
    if occurred_at is not None:
      # A newly inserted state was flushed above with this same timestamp.
      # Force it into the subsequent UPDATE so onupdate cannot replace the
      # accepted event clock merely because this scalar compares equal.
      flag_modified(st, 'updated_at')

  ev = RecommendationEventV2(
    id=str(uuid4()),
    user_id=user.id,
    product_id=payload.product_id,
    outfit_id=event_outfit_id,
    event_type=canonical_event_type,
    event_weight=weight,
    meta_json=meta,
    created_at=occurred_at or datetime.now(timezone.utc),
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
      changes = reduce_legacy_taste(tp, legacy_product_facts(p),
        event_type=canonical_event_type, meta=meta, weight=weight)
      for field, value in changes.items():
        setattr(tp, field, value)
      tp.updated_at = occurred_at or datetime.now(timezone.utc)
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
  return {'status': 'ok', 'event_type': canonical_event_type, 'weight': weight}
