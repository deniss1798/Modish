"""Request-local prospective dislike inputs, without ORM writes or owner locks.

Only a read-only ranking scope may install this overlay. It is not event
acceptance: publication must still validate revisions/evidence and persist the
same event and its effects atomically. The feedback API does not use it yet.
"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from types import SimpleNamespace

from ..catalog_normalize import normalize_category
from ..models import TasteProfile
from ..money import json_rubles
from .legacy_taste_reducer import legacy_product_facts, reduce_legacy_taste
from .recommendation_config import normalized_event_meta, recommendation_algorithm_metadata
from .user_twin_service import TasteFeatureSnapshot, preview_user_twin_features

_KEY = '_recommendation_feedback_preview'
_MISSING = object()
MAX_PREVIEW_EVENTS = 200


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


@dataclass(frozen=True)
class TasteSnapshot:
    values_json: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_profile(cls, taste):
        values = {column.name: getattr(taste, column.name) for column in TasteProfile.__table__.columns
            if column.name not in {'created_at', 'updated_at'}}
        return cls(json.dumps(values, ensure_ascii=True, separators=(',', ':')),
            taste.created_at, taste.updated_at)

    def copy_values(self):
        return SimpleNamespace(**json.loads(self.values_json), created_at=self.created_at, updated_at=self.updated_at)


@dataclass(frozen=True)
class PreviewEvent:
    product_id: str
    event_type: str
    metadata_json: str
    occurred_at: datetime
    brand: str
    category: str

    def metadata(self):
        return json.loads(self.metadata_json)

    def behavior_row(self, *, database_clock=None):
        return (self.product_id, self.brand, self.category, self.event_type, self.metadata(),
            self.occurred_at if database_clock is None else database_clock)


@dataclass(frozen=True)
class FeedbackPreview:
    user_id: str
    as_of: datetime
    taste: TasteSnapshot
    features: tuple[TasteFeatureSnapshot, ...]
    events: tuple[PreviewEvent, ...]
    touched: tuple[tuple[str, str], ...]

    def __post_init__(self):
        if (self.taste.copy_values().user_id != self.user_id or
                any(feature.user_id != self.user_id for feature in self.features)):
            raise ValueError('Feedback preview owner mismatch')
        if len(self.events) > MAX_PREVIEW_EVENTS or any(
                _utc(event.occurred_at) > _utc(self.as_of) for event in self.events):
            raise ValueError('Feedback preview event clock or count mismatch')

    def taste_copy(self):
        return self.taste.copy_values()


def preview_dislike(taste, feature_rows, product, *, user_id, meta=None, occurred_at, previous=None):
    """Pure data reduction; previous enables a prospective sequence of signals.

    This narrowly models product-event dislikes, including reason-specific
    semantics. Intent's task-only reasons must continue bypassing permanent
    feedback in its orchestration layer.
    """
    occurred_at = _utc(occurred_at)
    if taste is None or taste.user_id != user_id:
        raise ValueError('Taste preview owner mismatch')
    if previous is not None:
        if previous.user_id != user_id or occurred_at < _utc(previous.as_of):
            raise ValueError('Feedback preview owner or clock mismatch')
        taste, feature_rows = previous.taste_copy(), previous.features
    events = previous.events if previous is not None else ()
    if len(events) >= MAX_PREVIEW_EVENTS:
        raise ValueError('Too many prospective feedback events')
    if product is None or not product.id:
        raise ValueError('Feedback preview requires a current product')
    metadata = normalized_event_meta(event_type='dislike', meta=deepcopy(meta))
    for key, value in recommendation_algorithm_metadata(scenario=str(metadata.get('scenario') or '') or None).items():
        metadata.setdefault(key, value)
    metadata.update(observed_price=json_rubles(product.price) if product.currency in {'RUB', 'RUR'} else 0,
        observed_brand=str(product.brand or ''), observed_category=normalize_category(product.category))
    projected = TasteSnapshot.from_profile(taste).copy_values()
    for field, value in reduce_legacy_taste(projected, legacy_product_facts(product),
            event_type='dislike', meta=metadata).items():
        setattr(projected, field, value)
    projected.updated_at = occurred_at
    twin = preview_user_twin_features(feature_rows, user_id=user_id, product=product,
        event_type='dislike', meta=metadata, occurred_at=occurred_at)
    if twin.touched:
        projected.profile_version = int(projected.profile_version) + 1
    event = PreviewEvent(product.id, 'dislike', json.dumps(metadata, ensure_ascii=True,
        separators=(',', ':')), occurred_at, str(product.brand or ''), str(product.category or ''))
    return FeedbackPreview(user_id, occurred_at, TasteSnapshot.from_profile(projected), twin.features,
        (*events, event), tuple(sorted(set(previous.touched if previous is not None else ()) | set(twin.touched))))


@dataclass
class _PreviewScope:
    preview: FeedbackPreview
    behavior: dict | None = None


def current_feedback_preview(db, user_id, *, as_of=None):
    scope = db.info.get(_KEY)
    if scope is None or scope.preview.user_id != user_id:
        return None
    if not db.info.get('recommendation_read_only'):
        raise ValueError('Feedback preview requires read-only ranking')
    checked_at = as_of or db.info.get('recommendation_as_of')
    if checked_at is None or _utc(checked_at) != _utc(scope.preview.as_of):
        raise ValueError('Feedback preview clock mismatch')
    return scope.preview


@contextmanager
def use_feedback_preview(db, preview):
    """Install for one read-only Session scope, restoring nested callers safely."""
    if not isinstance(preview, FeedbackPreview):
        raise TypeError('Expected typed FeedbackPreview')
    if not db.info.get('recommendation_read_only') or db.new or db.dirty or db.deleted:
        raise ValueError('Feedback preview requires a clean read-only ranking Session')
    checked_at = db.info.get('recommendation_as_of')
    if checked_at is None or _utc(checked_at) != _utc(preview.as_of):
        raise ValueError('Feedback preview clock mismatch')
    previous = db.info.get(_KEY, _MISSING)
    db.info[_KEY] = _PreviewScope(preview)
    try:
        yield preview
    finally:
        if previous is _MISSING:
            db.info.pop(_KEY, None)
        else:
            db.info[_KEY] = previous


def cached_preview_behavior(db, user_id, *, as_of):
    if current_feedback_preview(db, user_id, as_of=as_of) is None:
        return None
    return deepcopy(db.info[_KEY].behavior)


def cache_preview_behavior(db, user_id, behavior, *, as_of):
    if current_feedback_preview(db, user_id, as_of=as_of) is not None:
        db.info[_KEY].behavior = deepcopy(behavior)


def merge_behavior_rows(rows, preview, *, as_of, database_clocks=None):
    """Merge the prospective stream by clock; do not materialize user history."""
    from datetime import timedelta
    minimum = _utc(as_of) - timedelta(days=90)
    clocks = tuple(database_clocks) if database_clocks is not None else tuple(event.occurred_at for event in preview.events)
    if len(clocks) != len(preview.events):
        raise ValueError('Feedback preview database clock count mismatch')
    pending = iter(sorted(((event, clock) for event, clock in zip(preview.events, clocks)
        if _utc(event.occurred_at) >= minimum), key=lambda item: _utc(item[0].occurred_at), reverse=True))
    item = next(pending, None)
    for row in rows:
        while item is not None and _utc(item[0].occurred_at) > _utc(row[5]):
            yield item[0].behavior_row(database_clock=item[1])
            item = next(pending, None)
        yield row
    while item is not None:
        yield item[0].behavior_row(database_clock=item[1])
        item = next(pending, None)
