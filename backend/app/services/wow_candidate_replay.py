"""Complete bounded candidate cohorts, owned by one read-only WOW transaction."""
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import weakref

from sqlalchemy import func, select

from ..models import Product
from .recommendation_context import ShoppingContext
from .recommendation_revision import profile_input_signature
from .personalization_scoring import AffinityScorer

_KEY = '_wow_candidate_replay'


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


@dataclass
class CandidateCohort:
    hidden: frozenset
    keys: frozenset
    rows: dict = field(default_factory=dict)
    evaluations: dict = field(default_factory=dict)
    count: int = 0
    overflow: bool = False

    def add(self, category, product, limit):
        if self.overflow:
            return
        if self.count >= limit:
            self.overflow = True
            self.rows.clear()
            self.evaluations.clear()
            return
        self.rows.setdefault(category, []).append(product)
        self.count += 1


class WowCandidateReplay:
    def __init__(self, db, user_id, signature, scenarios, limit):
        self.session = weakref.ref(db)
        self.transaction = db.get_transaction()
        self.user_id = user_id
        self.signature = signature
        self.scenarios = frozenset(scenarios)
        self.limit = limit
        self.revision = None
        self.cohorts = {}
        self.clock_safe = None
        self.last_clock = _utc(db.info['recommendation_as_of'])
        self.disabled = False

    def plan(self, db, user, *, scenario, context, hidden, keys, stable_conditions,
             restrict_categories, filters, source, required_roles, scorer):
        if (self.disabled or scenario not in self.scenarios
                or restrict_categories or filters is not None or source is not None or required_roles
                or not isinstance(context, ShoppingContext) or type(scorer) is not AffinityScorer):
            return None, None
        if (self.session() is not db or db.get_transaction() is not self.transaction
                or not db.info.get('recommendation_read_only') or user.id != self.user_id
                or db.new or db.dirty or db.deleted):
            self.disabled = True
            self.cohorts.clear()
            return None, None
        if self.revision is None:
            self.revision = user.recommendation_revision
        if (user.recommendation_revision != self.revision
                or profile_input_signature(db, user.id) != self.signature):
            self.disabled = True
            self.cohorts.clear()
            return None, None
        now = datetime.now(timezone.utc)
        if now < self.last_clock:
            self.disabled = True
            self.cohorts.clear()
            return None, None
        self.last_clock = now
        if self.clock_safe is None:
            # Without future observations, advancing the clock can only remove
            # stock. Future rows outside the first SQL window could later enter.
            latest = db.scalar(select(func.max(Product.last_seen_in_feed_at)).where(*stable_conditions))
            self.clock_safe = latest is None or _utc(latest) <= _utc(db.info['recommendation_as_of'])
        if not self.clock_safe:
            return None, None
        # The profile signature covers stored inputs; explicitly supplied score
        # preferences and feature/exposure maps must also match before reuse.
        try:
            score_inputs = (tuple(sorted(scorer.taste_features.items())),
                tuple(sorted(scorer.seen_counts.items())), scorer.preferred_colors,
                scorer.preferred_styles, scorer.requested_categories, scorer.context)
            key = (scenario, context, score_inputs)
            hash(key)
        except (TypeError, ValueError):
            return None, None
        cohort = self.cohorts.get(key)
        if cohort is not None:
            if cohort.hidden.issubset(hidden) and cohort.keys.issubset(keys):
                return cohort, None
            return None, None  # A less restrictive request needs a full scan.
        capture = CandidateCohort(frozenset(hidden), frozenset(keys))
        return None, (key, capture)

    def finish(self, capture):
        if capture is not None and not self.disabled:
            key, cohort = capture
            if not cohort.overflow:
                self.cohorts[key] = cohort

    def capture(self, capture, category, product):
        # The bound covers the entire transaction, including distinct contexts.
        remaining = self.limit - sum(cohort.count for cohort in self.cohorts.values())
        capture[1].add(category, product, remaining)


@contextmanager
def wow_candidate_replay_scope(db, user_id, profile_signature, scenarios=('daily',), limit=8192):
    if not 1 <= limit <= 8192:
        raise ValueError('WOW candidate replay limit must be between 1 and 8192')
    previous = db.info.get(_KEY)
    scope = WowCandidateReplay(db, user_id, profile_signature, scenarios, limit)
    db.info[_KEY] = scope
    try:
        yield scope
    finally:
        scope.cohorts.clear()
        if previous is None:
            db.info.pop(_KEY, None)
        else:
            db.info[_KEY] = previous


def candidate_replay_plan(db, user, **kwargs):
    scope = db.info.get(_KEY)
    if scope is None:
        return None, None
    return scope.plan(db, user, **kwargs)


def finish_candidate_replay(db, capture):
    scope = db.info.get(_KEY)
    if scope is not None:
        scope.finish(capture)


def capture_candidate_replay(db, capture, category, product):
    scope = db.info.get(_KEY)
    if scope is not None:
        scope.capture(capture, category, product)
