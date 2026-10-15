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
from .recommendation_budget import check_ranking_budget

COMPLEMENT_ROLES = {
    "top": ("bottom", "shoes"),
    "bottom": ("top", "shoes"),
    "shoes": ("top", "bottom"),
    "one_piece": ("shoes",),
    "outerwear": ("top", "bottom", "shoes"),
}


def complementary_products(db: Session, user: User, anchor: Product, *, limit: int = 6) -> list[Product]:
    db.info['prepared_product_complement_excluded_ids'] = (anchor.id,)
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
        db.info['prepared_product_complement_excluded_ids'] = tuple(sorted({anchor.id} | {
            product_id for product_id, reaction in (activation.swipes_json or {}).items()
            if reaction in {'dislike', 'unavailable'}}))
        keys = product_identity_keys(anchor)
        rows = []
        for product in candidate_pool(db, user, activation, exclude_ids={anchor.id}):
            check_ranking_budget(db)
            if (product_role(product) in roles and not (product_identity_keys(product) & keys)
                    and pair_compatible(anchor, product)
                    and as_rubles(product.price) + as_rubles(anchor.price) <= as_rubles(fit.budget_max)):
                rows.append(product)
    else:
        excluded = _excluded_product_ids(db, user, scenario=scenario)
        db.info['prepared_product_complement_excluded_ids'] = tuple(sorted(excluded | {anchor.id}))
        rows = _catalog_slot_candidates(db, user, slots=set(roles), scenario=scenario,
            excluded=excluded, existing_ids={anchor.id},
            limit=30, anchor=anchor)
    # Alternate missing roles so a run of trousers cannot crowd out all shoes.
    buckets = [[p for p in rows if product_role(p) == role] for role in roles]
    for bucket in buckets:
        check_ranking_budget(db)
        bucket.sort(key=lambda p: abs(as_rubles(p.price) - as_rubles(anchor.price)))
    return [p for group in zip_longest(*buckets) for p in group if p is not None][:max(1, min(12, limit))]
