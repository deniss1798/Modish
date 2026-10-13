"""Hard admission before ranking; unknown fit/stock is never a score penalty."""
from dataclasses import dataclass

from .catalog.catalog_quality import product_is_feed_eligible
from .catalog.rule_filters import product_gender_compatible
from .catalog.size_resolver import exact_size_match
from .catalog.stock_freshness import availability_snapshot
from .fit_size_profile import requested_size_for_product


@dataclass(frozen=True)
class EligibilityDecision:
    eligible: bool
    reason: str | None = None


def evaluate_eligibility(product, fit, *, filters=None, budget_max=None) -> EligibilityDecision:
    if fit is None or fit.gender_target not in {'womenswear', 'menswear', 'unisex'}:
        return EligibilityDecision(False, 'fit_profile_unknown')
    if not product_gender_compatible(product, fit.gender_target):
        return EligibilityDecision(False, 'gender_mismatch')
    if filters and not filters.matches(product):
        return EligibilityDecision(False, 'explicit_filter_mismatch')
    requested, system = requested_size_for_product(fit, product)
    if requested is None:
        return EligibilityDecision(False, 'size_unknown')
    if not exact_size_match(product, requested, requested_system=system):
        return EligibilityDecision(False, 'size_unavailable_or_unknown')
    availability = availability_snapshot(product)
    if not availability.available:
        return EligibilityDecision(False, availability.reason)
    maximum = budget_max if budget_max is not None else fit.budget_max
    if maximum is None or int(maximum) <= 0:
        return EligibilityDecision(False, 'budget_unknown')
    if not 0 < int(product.price or 0) <= int(maximum):
        return EligibilityDecision(False, 'budget_exceeded')
    if not product_is_feed_eligible(product):
        return EligibilityDecision(False, 'catalog_quality')
    return EligibilityDecision(True)
