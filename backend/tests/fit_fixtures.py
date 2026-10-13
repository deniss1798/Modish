"""Explicitly confirmed sizes for synthetic recommendation fixtures.

These tests use letter-sized synthetic footwear as well as clothing. Tests of
real footwear/unit contracts pass their own shoe_size/system or construct an
unconfirmed FitProfile directly. No production defaults are modified here.
"""
from app.models import FitProfile
from app.services.fit_size_profile import submitted_sizes, update_size_profile


def confirmed_fit(*, shoe_size=None, shoe_system=None, **fields):
    fit = FitProfile(**fields)
    clothing = fields.get('clothing_size')
    constraints = fields.get('style_constraints') or {}
    update_size_profile(fit, sizes=submitted_sizes(clothing_size=clothing,
        clothing_system=constraints.get('clothing_size_system'),
        shoe_size=shoe_size if shoe_size is not None else clothing, shoe_system=shoe_system), height_confirmed=True)
    return fit
