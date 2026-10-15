"""Retrieve complementary roles from the catalogue, independently of feed pages."""
from itertools import zip_longest
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..models import ActivationSession, FitProfile, Product, User
from ..money import as_rubles
from .garment_roles import product_role
from .outfit_engine_v2 import _catalog_slot_candidates, _excluded_product_ids, _hard_ok
from .outfit_quality import is_sports_item, pair_compatible
from .product_identity import product_identity_keys
from .catalog.rule_filters import load_rules_by_source_id

COMPLEMENT_ROLES = {
    "top": ("bottom", "shoes"),
    "bottom": ("top", "shoes"),
    "shoes": ("top", "bottom"),
    "one_piece": ("shoes",),
    "outerwear": ("top", "bottom", "shoes"),
}


def complementary_products(db: Session, user: User, anchor: Product, *, limit: int = 6) -> list[Product]:
    roles = COMPLEMENT_ROLES.get(product_role(anchor), ())
    if not roles:
        return []
    fit = db.execute(select(FitProfile).where(FitProfile.user_id == user.id)).scalar_one_or_none()
    if not _hard_ok(anchor, fit=fit, rules_by_source=load_rules_by_source_id(db)):
        return []
    scenario = "gym" if is_sports_item(anchor) else "daily"
    activation = db.get(ActivationSession, user.id)
    if activation is not None:
        from .activation_service import candidate_pool, product_ok
        if not product_ok(anchor, fit, activation):
            return []
        keys = product_identity_keys(anchor)
        rows = [p for p in candidate_pool(db, user, activation, exclude_ids={anchor.id})
                if product_role(p) in roles and not (product_identity_keys(p) & keys)
                and pair_compatible(anchor, p) and as_rubles(p.price) + as_rubles(anchor.price) <= as_rubles(fit.budget_max)]
    else:
        rows = _catalog_slot_candidates(db, user, slots=set(roles), scenario=scenario,
            excluded=_excluded_product_ids(db, user, scenario=scenario), existing_ids={anchor.id},
            limit=30, anchor=anchor)
    # Alternate missing roles so a run of trousers cannot crowd out all shoes.
    buckets = [[p for p in rows if product_role(p) == role] for role in roles]
    for bucket in buckets:
        bucket.sort(key=lambda p: abs(as_rubles(p.price) - as_rubles(anchor.price)))
    return [p for group in zip_longest(*buckets) for p in group if p is not None][:max(1, min(12, limit))]
