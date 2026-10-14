"""One confirmed, category-specific exact size gate for personal recommendations.

Discovery is deliberately separate and never turns an unknown size into a fit
claim. This decision is reused by admission, legacy filters and MIE reasons.
"""
from dataclasses import dataclass

from .catalog.size_resolver import exact_size_match
from .fit_size_profile import requested_size_for_product


@dataclass(frozen=True)
class SizeGateDecision:
    eligible: bool
    reason: str | None = None


def evaluate_personal_size(product, fit) -> SizeGateDecision:
    try:
        requested, system = requested_size_for_product(fit, product)
    except (AttributeError, TypeError, ValueError):
        # Malformed old profile JSON is not evidence of a user's size.
        return SizeGateDecision(False, 'size_unknown')
    if not isinstance(requested, str) or not isinstance(system, str) or not requested or not system:
        return SizeGateDecision(False, 'size_unknown')
    if not exact_size_match(product, requested, requested_system=system):
        return SizeGateDecision(False, 'size_unavailable_or_unknown')
    return SizeGateDecision(True)
