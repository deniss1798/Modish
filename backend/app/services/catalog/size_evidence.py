"""Compatibility facade for callers of the original exact-size evidence gate."""
from .size_resolver import LETTER, explicit_size_system, exact_size_match


def confirmed_product_size(product, requested, *, requested_system=None):
    return exact_size_match(product, requested, requested_system=requested_system)
