"""Retrieve complementary roles from the catalogue, independently of feed pages."""
from itertools import zip_longest
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..models import FitProfile, Product, User
from .garment_roles import product_role
from .outfit_engine_v2 import _catalog_slot_candidates, _excluded_product_ids, _hard_ok
from .outfit_quality import is_sports_item
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
    rows = _catalog_slot_candidates(db, user, slots=set(roles), scenario=scenario,
        excluded=_excluded_product_ids(db, user, scenario=scenario), existing_ids={anchor.id},
        limit=30, anchor=anchor)
    # Alternate missing roles so a run of trousers cannot crowd out all shoes.
    buckets = [[p for p in rows if product_role(p) == role] for role in roles]
    for bucket in buckets:
        bucket.sort(key=lambda p: abs(int(p.price or 0) - int(anchor.price or 0)))
    return [p for group in zip_longest(*buckets) for p in group if p is not None][:max(1, min(12, limit))]
