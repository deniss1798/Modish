"""Reason breakdown for current first-twenty refusals, never raw telemetry.

The caller freezes its exposure denominator first. A repeated event cannot add a
card, and a historical refusal without an authoritative reason stays unknown.
"""
from collections import Counter

from sqlalchemy import and_, func, or_, select

from ..models import ActivationExposure, ActivationSession, RecommendationEventV2
from .recommendation_config import VALID_DISLIKE_REASONS


def first_session_dislike_reasons(db, *, exposures, since, now):
    remaining = {(row.user_id, row.product_id) for row in exposures if row.reaction == 'dislike'}
    disliked = len(remaining)
    reasons = Counter()
    if remaining:
        # Join on both owner and product. A shared catalogue SKU must not
        # borrow another person's reason or a Style Swipe/telemetry signal.
        event = RecommendationEventV2
        exposure = ActivationExposure
        surface = func.lower(func.trim(event.meta_json['surface'].as_string()))
        query = select(event.user_id, event.product_id, event.meta_json).join(exposure,
            and_(exposure.user_id == event.user_id, exposure.product_id == event.product_id))
        query = query.join(ActivationSession, ActivationSession.user_id == exposure.user_id).where(
            ActivationSession.completed_at >= since, ActivationSession.completed_at <= now,
            exposure.position.between(1, 20), exposure.shown_at >= ActivationSession.completed_at,
            exposure.shown_at <= now, event.event_type == 'dislike',
            event.created_at >= since, event.created_at >= exposure.shown_at, event.created_at <= now,
            or_(surface.is_(None), surface.not_in(['style_swipe', 'catalogue_discovery'])))
        # The latest eligible refusal supplies one reason for this card.
        # Streaming and the frozen pair set bound memory by the first20 cohort,
        # rather than the number of repeated reactions or arbitrary reason text.
        query = query.order_by(event.user_id, event.product_id,
            event.created_at.desc(), event.id.desc()).execution_options(yield_per=500)
        with db.execute(query) as events:
            for user_id, product_id, metadata in events:
                pair = (user_id, product_id)
                if pair not in remaining:
                    continue
                remaining.remove(pair)
                raw = metadata.get('reason') if isinstance(metadata, dict) else None
                reason = raw.strip().lower() if isinstance(raw, str) and len(raw) <= 64 else None
                if reason in VALID_DISLIKE_REASONS:
                    reasons[reason] += 1
                if not remaining:
                    break
    specified = sum(reasons.values())
    return {'disliked_cards': disliked, 'with_reason_cards': specified,
        'without_reason_cards': disliked - specified,
        'top_reasons': [{'reason': reason, 'count': count,
            'share_percent': round(100 * count / disliked, 2)}
            for reason, count in sorted(reasons.items(), key=lambda item: (-item[1], item[0]))],
        'basis': 'Текущие отказы среди первых 20 уникальных показанных карточек пользователей, завершивших онбординг за период. Для каждой карточки берётся последняя подтверждённая причина отказа после показа и в пределах периода. Повторные события и Style Swipe не увеличивают счётчик. Неизвестные причины учитываются без текста.'}
