"""Availability expires at read time, even if an importer or scheduler stops.

Only a source observation may renew stock. Processing, viewing, editing and
replaying a product do not establish its availability.
"""
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass

from ...models import Product

MAX_STOCK_AGE = timedelta(hours=72)
MAX_CLOCK_SKEW = timedelta(minutes=5)


@dataclass(frozen=True)
class AvailabilitySnapshot:
    state: str
    reason: str | None
    observed_at: datetime | None
    # A feed observation is not a measured probability of stock at checkout.
    confidence: float | None = None

    @property
    def available(self) -> bool:
        return self.state == 'available'


def _utc(value: datetime) -> datetime:
    # SQLite returns naive timestamps; stored naive observations are UTC.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def stock_observation_problem(product, *, checked_at: datetime | None = None) -> str | None:
    observed = product.last_seen_in_feed_at
    if observed is None:
        return "unknown_stock_observation_time"
    now = _utc(checked_at or datetime.now(timezone.utc))
    observed = _utc(observed)
    if observed > now + MAX_CLOCK_SKEW:
        return "invalid_stock_observation_time"
    if observed < now - MAX_STOCK_AGE:
        return "stale_availability"
    return None


def fresh_stock_conditions(*, checked_at: datetime | None = None) -> list:
    """Apply before a candidate limit, using the same bounds as the read gate."""
    now = _utc(checked_at or datetime.now(timezone.utc))
    return [
        Product.last_seen_in_feed_at.is_not(None),
        Product.last_seen_in_feed_at >= now - MAX_STOCK_AGE,
        Product.last_seen_in_feed_at <= now + MAX_CLOCK_SKEW,
    ]


def availability_snapshot(product, *, checked_at: datetime | None = None) -> AvailabilitySnapshot:
    observed = getattr(product, 'last_seen_in_feed_at', None)
    def result(state, reason=None):
        return AvailabilitySnapshot(state, reason, observed)
    if product.is_deleted_from_feed:
        return result('unavailable', 'removed_from_feed')
    if product.is_active != 1:
        return result('unavailable', 'inactive_product')
    reported = product.availability_status or ''
    if reported in {'out_of_stock', 'unavailable'}:
        return result('unavailable', 'out_of_stock')
    if reported not in {'in_stock', 'available'}:
        return result('unknown', 'unknown_availability')
    if not product.is_available:
        return result('unavailable', 'out_of_stock')
    problem = stock_observation_problem(product, checked_at=checked_at)
    if problem:
        return result('stale' if problem == 'stale_availability' else 'unknown', problem)
    return result('available')


def available_product_conditions(*, checked_at: datetime | None = None) -> list:
    """SQL equivalent of snapshot.available, to apply before any candidate cap."""
    return [Product.is_active == 1, Product.is_available == 1,
        Product.is_deleted_from_feed == 0,
        Product.availability_status.in_(['in_stock', 'available']),
        *fresh_stock_conditions(checked_at=checked_at)]
