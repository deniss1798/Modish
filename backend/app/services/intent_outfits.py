"""Bounded composition using the existing compatibility rules and exact Fit gate."""
from functools import lru_cache
from itertools import product as combinations
from datetime import datetime, timezone
import heapq
from uuid import uuid5, NAMESPACE_URL
from sqlalchemy import select, func
from ..models import Product
from ..money import as_rubles, json_rubles
from ..catalog_normalize import normalize_category
from ..schemas.api_product import product_to_api
from .catalog.stock_freshness import available_product_conditions
from .garment_roles import product_role
from .intent_eligibility import intent_facts, intent_product_problem, intent_commerce_problem, constraint_problem
from .mie_scoring import load_user_taste_feature_map
from .personalization_scoring import AffinityScorer, load_ranking_seen_counts
from .profile_repository import load_preferences
from .recommendation_context import context_for
from .outfit_quality import pair_compatible, outfit_compatible
from .product_identity import product_identity_keys
from .recommendation_budget import check_ranking_budget
from .outfit_policy import MAX_RECOMMENDED_OUTFITS, requested_outfit_count

SLOT_OPTIONS = (('top', 'bottom', 'shoes'), ('one_piece', 'shoes'))


def outfit_problem(products, fit, spec):
    if set(products) not in [set(option) for option in SLOT_OPTIONS]:
        return 'incomplete_outfit'
    if any(product_role(p) != slot or intent_product_problem(p, fit, spec) for slot, p in products.items()):
        return 'ineligible_product'
    if not set(spec.requested_categories) <= {normalize_category(p.category) for p in products.values()}:
        return 'missing_requested_category'
    total = sum((as_rubles(p.price) for p in products.values()), as_rubles(0))
    maximum = min(fit.budget_max, spec.budget_max or fit.budget_max)
    if total > maximum or total < (spec.budget_min or 0):
        return 'outfit_budget'
    if not outfit_compatible(list(products.values())):
        return 'incompatible_items'
    return None


def serialize_outfit(products, fit, spec, *, intent_id, version):
    from .fit_size_profile import requested_size_for_product
    from .catalog.size_resolver import resolve_size, resolve_product_sizes
    from .personal_size_gate import evaluate_personal_size
    size_matches = {}
    for slot, product in products.items():
        if not evaluate_personal_size(product, fit).eligible:
            continue
        requested, system = requested_size_for_product(fit, product)
        wanted = resolve_size(requested, size_system=system,
            category=product.category or product.category_name or '')
        if not wanted.known:
            continue
        matched = next((size for size in resolve_product_sizes(product)
            if size.known and size.availability == 'available'
            and size.size_system == wanted.size_system
            and size.normalized_size == wanted.normalized_size), None)
        if matched is not None:
            size_matches[slot] = matched.to_dict()
    items = {slot: p.id for slot, p in products.items()}
    key = f'{intent_id}:{version}:' + ':'.join(items[slot] for slot in sorted(items))
    maximum = min(fit.budget_max, spec.budget_max or fit.budget_max)
    confirmed = bool(products) and len(size_matches) == len(products)
    size_reason = 'Размеры указаны в наличии. ' if confirmed else ''
    return {'id': str(uuid5(NAMESPACE_URL, key)), 'items': items,
        'products': {slot: product_to_api(p) for slot, p in products.items()},
        'size_matches': size_matches,
        'total_price': json_rubles(sum((as_rubles(p.price) for p in products.values()), as_rubles(0))), 'style_direction': spec.occasion or 'daily',
        'reason': f'{size_reason}Весь комплект — до {maximum:,} ₽.'.replace(',', ' '),
        'confidence': {'fit': 'confirmed' if confirmed else 'unknown',
            'availability': 'feed_observed' if confirmed else 'unknown', 'compatibility': 'rule_based'},
        'is_saved': False}


def build_intent_outfits(db, user, spec, *, intent_id, version, locked=None, excluded_ids=(), limit=MAX_RECOMMENDED_OUTFITS, stats=None):
    limit = requested_outfit_count(limit)
    profile, fit, taste = load_preferences(db, user)
    if not fit or fit.gender_target not in {'womenswear', 'menswear', 'unisex'} or not fit.budget_max:
        return []
    locked = locked or {}
    if any(product_role(p) != slot or intent_product_problem(p, fit, spec) for slot, p in locked.items()):
        return []
    maximum = min(fit.budget_max, spec.budget_max or fit.budget_max)
    context = context_for(spec.occasion or 'daily', budget_max=maximum)
    scorer = AffinityScorer(profile, taste, fit=fit,
        seen_counts=load_ranking_seen_counts(db, user_id=user.id,
            as_of=db.info.get('recommendation_as_of') or datetime.now(timezone.utc)),
        taste_features=load_user_taste_feature_map(db, user_id=user.id), context=context,
        preferred_colors=spec.preferred_colors, preferred_styles=spec.preferred_styles,
        requested_categories=spec.requested_categories)
    stats = stats if stats is not None else {}
    base = [Product.source != 'demo']
    if fit.gender_target in {'womenswear', 'menswear'}:
        base.append(Product.gender_target.in_([fit.gender_target, 'unisex']))
    if spec.excluded_categories:
        base.append(Product.category.not_in(spec.excluded_categories))
    stats['catalog_count'] = db.scalar(select(func.count()).select_from(Product).where(*base))
    stats['available_count'] = db.scalar(select(func.count()).select_from(Product).where(*base, *available_product_conditions()))
    conditions = [*base, *available_product_conditions(), Product.price > 0, Product.price <= maximum]
    if excluded_ids:
        conditions.append(Product.id.not_in(list(excluded_ids)))
    # Inspect only sold-garment fields first. Do not load descriptions/raw feed
    # blobs or rank clothing if a mandatory outfit role cannot be filled.
    role_ids = {}
    for row in db.execute(select(Product.id, Product.title, Product.category,
            Product.category_name, Product.subcategory).where(*conditions)
            .order_by(Product.id).execution_options(yield_per=500)):
        check_ranking_budget(db)
        slot = product_role(row)
        if slot in {'top', 'bottom', 'one_piece', 'shoes'} and slot not in locked:
            role_ids.setdefault(slot, []).append(row.id)
    options = [slots for slots in SLOT_OPTIONS if set(locked) <= set(slots)
        and all(slot in locked or role_ids.get(slot) for slot in slots)]
    if not options:
        return []
    top, cheap = {}, {}
    seen = set()
    for excluded in db.scalars(select(Product).where(Product.id.in_(list(excluded_ids)))):
        seen.update(product_identity_keys(excluded))
    # Shoes are common to every supported composition. Probe that role first;
    # if all fail exact admission, no other full product rows need loading.
    required = set().union(*map(set, options)) - set(locked)
    ordered_slots = sorted(required, key=lambda slot: (slot != 'shoes', len(role_ids[slot]), slot))
    for slot in ordered_slots:
        ids = role_ids[slot]
        for offset in range(0, len(ids), 200):
            # Preserve the thin scan's stable first-eligible identity policy;
            # an IN predicate alone does not guarantee hydration row order.
            for product in db.scalars(select(Product).where(Product.id.in_(ids[offset:offset+200]))
                    .order_by(Product.id)):
                check_ranking_budget(db)
                # An import may change classification between the thin index
                # and this full read. A stale role must never fill a wrong slot.
                if product_role(product) != slot:
                    continue
                problem = intent_commerce_problem(product, fit, spec)
                stats['candidate_count_after_stock_budget'] = stats.get('candidate_count_after_stock_budget', 0)+1
                if problem not in {'fit_profile_unknown', 'gender_mismatch', 'explicit_filter_mismatch'}:
                    stats['size_checked'] = stats.get('size_checked', 0)+1
                    if not str(problem or '').startswith('size_'):
                        stats['size_passed'] = stats.get('size_passed', 0)+1
                if problem:
                    continue
                facts = intent_facts(product)
                if constraint_problem(product, spec, facts):
                    continue
                stats['eligible_count'] = stats.get('eligible_count', 0)+1
                keys = product_identity_keys(product)
                if keys & seen:
                    continue
                seen.update(keys)
                score = scorer.score(product)
                # Keep each finalist's exact common score alongside both heaps.
                # A full scan may evict early winners from the bounded scorer;
                # composition must not parse/rank them a second time.
                for pool, entry, cap in ((top, (score, product.id, product, score), 18),
                        (cheap, (-product.price, product.id, product, score), 6)):
                    heap = pool.setdefault(slot, [])
                    if len(heap) < cap:
                        heapq.heappush(heap, entry)
                    elif entry[:2] > heap[0][:2]:
                        heapq.heapreplace(heap, entry)
        if not top.get(slot):
            options = [option for option in options if slot not in option]
            if not options:
                return []
    pools = {slot: list({p.id: p for _, _, p, _ in top.get(slot, []) + cheap.get(slot, [])}.values())
             for slot in set(top) | set(cheap)}
    pools.update({slot: [p] for slot, p in locked.items()})
    all_products = {p.id: p for values in pools.values() for p in values}
    scores = {p.id: score for slot in set(top) | set(cheap)
        for _, _, p, score in top.get(slot, []) + cheap.get(slot, [])}
    scores.update({p.id: scorer.score(p) for p in locked.values()})

    @lru_cache(maxsize=2048)
    def compatible(left, right):
        return pair_compatible(all_products[left], all_products[right])

    best = []
    for slots in SLOT_OPTIONS:
        if not set(locked) <= set(slots) or any(not pools.get(slot) for slot in slots):
            continue
        for group in combinations(*(pools[slot] for slot in slots)):
            check_ranking_budget(db)
            total = sum(p.price for p in group)
            if not (spec.budget_min or 0) <= total <= maximum:
                continue
            if not set(spec.requested_categories) <= {normalize_category(p.category) for p in group}:
                continue
            if not all(compatible(a.id, b.id) for i, a in enumerate(group) for b in group[i + 1:]):
                continue
            score = sum(scores[p.id] for p in group) / len(group)
            identity = ':'.join(p.id for p in group)
            entry = (score, identity, dict(zip(slots, group)))
            if len(best) < 200:
                heapq.heappush(best, entry)
            elif entry[:2] > best[0][:2]:
                heapq.heapreplace(best, entry)
    result, previous_keys = [], []
    for _, _, products in sorted(best, key=lambda row: row[:2], reverse=True):
        keys = set().union(*(product_identity_keys(p) for p in products.values()))
        if not locked and any(sum(bool(product_identity_keys(p) & old) for p in products.values()) > len(products) - 2 for old in previous_keys):
            continue
        previous_keys.append(keys)
        result.append(serialize_outfit(products, fit, spec, intent_id=intent_id, version=version))
        if len(result) >= limit:
            break
    return result
