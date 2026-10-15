"""Rule-based activation: durable taste learning and honest first 20 cards."""
from __future__ import annotations
from ..money import as_rubles

from collections import Counter
from contextlib import closing
from datetime import datetime, timedelta, timezone
from math import ceil
import heapq
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import String, case, cast, func, or_, select

from ..catalog_normalize import normalize_category, normalize_size_token
from ..models import ActivationSession, ActivationExposure, FitProfile, Product, RecommendationEventV2, StyleProfile, User
from .catalog.rule_filters import load_rules_by_source_id
from .catalog.candidate_scan import iter_size_candidates
from .catalog.size_resolver import may_match_flat_size
from .catalog.stock_freshness import fresh_stock_conditions
from .garment_roles import product_role
from .fit_size_profile import requested_size_for_product
from .product_identity import product_identity_keys

# Keep the original policy/scoring names available to existing callers.
from .activation_policy import (
    STYLES, STYLE_LABELS, CATEGORIES, SEGMENT_BUDGETS, EXCLUDED_SOURCES,
    features, confirmed_size, product_ok,
)
from .profile_repository import load_preferences as _preferences
from .personalization_scoring import AffinityScorer, affinity_score as _affinity, load_ranking_seen_counts
from .recommendation_revision import profile_input_writer, accept_personal_change, owner_mutation

MIN_SWIPES = 15
SWIPE_TARGET = 20


def rated_count(session):
    return sum(r in {'like', 'dislike'} for r in (session.swipes_json or {}).values())


def can_complete(session):
    return rated_count(session) >= MIN_SWIPES and 'like' in (session.swipes_json or {}).values()


def lock_user(db, user):
    db.execute(select(User.id).where(User.id == user.id).with_for_update()).scalar_one()


def legacy_has_taste(db, user, profile=None):
    profile = profile or db.scalar(select(StyleProfile).where(StyleProfile.user_id == user.id))
    if not profile or not profile.completed_at:
        return False
    count = db.scalar(select(func.count(func.distinct(RecommendationEventV2.product_id))).where(
        RecommendationEventV2.user_id == user.id,
        RecommendationEventV2.event_type.in_(['like', 'save', 'dislike', 'skip']),
    )) or 0
    return count >= MIN_SWIPES


def require_warm_feed(db, user):
    from .fit_size_profile import missing_fit_requirements
    fit = db.scalar(select(FitProfile).where(FitProfile.user_id == user.id))
    if missing_fit_requirements(fit):
        raise HTTPException(status_code=409, detail='Подтвердите рост и размеры в профиле. Ваши предпочтения сохранены.')
    session = db.get(ActivationSession, user.id)
    if (session and session.completed_at) or (not session and legacy_has_taste(db, user)):
        return session
    raise HTTPException(status_code=409, detail='Сначала настройте стиль и оцените хотя бы 15 вещей в Style Swipe.')


def _required_roles_available(db, *, required_roles, conditions, hidden, hidden_keys,
                              fit, session, rules, filters, context):
    """Prove mandatory-role absence before loading/ranking other garments.

    This probe never selects candidates or changes identity order. A positive
    result continues through the original full-catalog ranking; no capped or
    approximate category query may establish that a role is unavailable.
    """
    query = select(Product.id, Product.title, Product.category, Product.category_name,
                   Product.subcategory, Product.available_sizes).where(*conditions)
    if hidden:
        query = query.where(Product.id.notin_(hidden))
    loaded = {value.id: value for value in db.identity_map.values()
              if isinstance(value, Product)}

    def role_available(role):
        pending = []

        def admitted_batch():
            with db.scalars(select(Product).where(*conditions, Product.id.in_(pending))) as products:
                for product in products:
                    # Recheck classification on the full read: catalog updates
                    # between projections cannot fill a wrong role.
                    if product_role(product) != role or hidden_keys & product_identity_keys(product):
                        continue
                    if not product_ok(product, fit, session, rules=rules):
                        continue
                    if filters and filters.active and not filters.matches(product):
                        continue
                    if context is not None and not context.allows(product):
                        continue
                    return True
            return False

        with db.execute(query.execution_options(yield_per=500)) as role_rows:
            for row in role_rows:
                evidence = loaded.get(row.id, row)
                if product_role(evidence) != role:
                    continue
                requested, system = requested_size_for_product(fit, evidence)
                if not may_match_flat_size(evidence, requested, requested_system=system):
                    continue
                pending.append(row.id)
                if len(pending) >= 200:
                    if admitted_batch():
                        return True
                    pending.clear()
            return bool(pending) and admitted_batch()

    # Shoes are shared by current outfit templates. Even when a scenario also
    # requires top/bottom, missing footwear must not load full clothing rows.
    return all(role_available(role) for role in sorted(set(required_roles),
        key=lambda role: (role != 'shoes', role)))


def candidate_pool(db, user, session, *, exclude_ids=(), restrict_categories=False, filters=None,
                   context=None, taste_features=None, scorer=None, preferences=None, source=None,
                   required_roles=()):
    """Full-catalog queries per category; no fresh-item cutoff or embeddings."""
    profile, fit, taste = preferences or _preferences(db, user)
    from .mie_scoring import load_user_taste_feature_map
    from .user_twin_service import DNA_TASTE_DIMENSIONS
    if taste_features is None:
        taste_features = load_user_taste_feature_map(db, user_id=user.id)
    semantic_ranking = any(kind in DNA_TASTE_DIMENSIONS for kind, _ in taste_features)
    if not fit or not fit.budget_max:
        return []
    seen_counts = load_ranking_seen_counts(db, user_id=user.id,
        as_of=db.info.get('recommendation_as_of') or datetime.now(timezone.utc)) if scorer is None or not scorer.seen_counts_loaded else None
    if scorer is None:
        scorer = AffinityScorer(profile, taste, fit=fit, seen_counts=seen_counts,
            taste_features=taste_features, context=context)
    else:
        # Outfit/feed callers share this scorer with their later composition.
        # Bind actual Fit and action history before its first candidate score.
        scorer.bind_admission_context(fit=fit, seen_counts=seen_counts)
    # With autoflush disabled, full ORM reads retain pending product changes
    # that a thin database projection cannot see. Preserve that existing path
    # instead of making an absence claim from stale role/identity fields.
    probe_roles = required_roles if not any(isinstance(row, Product) for row in db.dirty) else ()
    rules = load_rules_by_source_id(db)
    hidden = set(map(str, exclude_ids))
    hidden.update(pid for pid, reaction in (session.swipes_json or {}).items() if reaction in {'dislike', 'unavailable'})
    keys = set()
    if hidden:
        # A mandatory-role probe needs only identity fields of rejected items;
        # do not materialize unrelated clothing merely to exclude its aliases.
        identity_query = (select(Product.id, Product.original_url, Product.source, Product.group_id,
                                 Product.vendor_code, Product.image_url) if probe_roles else select(Product))
        hidden_rows = db.execute(identity_query.where(Product.id.in_(hidden)))
        for product in (hidden_rows if probe_roles else hidden_rows.scalars()):
            keys.update(product_identity_keys(product))
    interests = {normalize_category(str(c)) for c in (fit.interest_categories or [])}
    interests.update(normalize_category(str(c)) for c in (taste.liked_categories or []))
    interests.update(normalize_category(str(c)) for c, value in (taste.category_weights or {}).items() if float(value) > 0)
    # An explicit category expresses current intent. Learned interests rank the
    # selection, but must not veto a request to explore shoes or outerwear.
    restrict_categories = restrict_categories and not (filters and filters.categories)
    wanted = set(profile.style_preferences or []) | set(taste.liked_styles or [])
    style_conditions = [cast(Product.style_tags, String).ilike(f'%"{s}"%') for s in sorted(wanted & STYLES)]
    style_match = or_(*style_conditions) if style_conditions else Product.id.is_(None)
    # Rank the SQL pool by learned signals BEFORE taking each category's 50
    # candidates; otherwise liked brands/colors beyond that cutoff never enter.
    affinity_order = case((style_match, .35), else_=0.0)
    for kind, column, scale in [('style', Product.style_tags, .22), ('color', Product.colors, .08), ('brand', Product.brand, .05)]:
        weights = getattr(taste, f'{kind}_weights') or {}
        for value, weight in sorted(weights.items(), key=lambda pair: abs(float(pair[1])), reverse=True)[:30]:
            condition = func.lower(column) == str(value).lower() if kind == 'brand' else cast(column, String).ilike(f'%"{value}"%')
            affinity_order += case((condition, scale * max(-1., min(1., float(weight) / 6))), else_=0.0)
    # Popularity is scoped to women and the same price segment by the outer query.
    popularity = select(RecommendationEventV2.product_id.label('pid'), func.count().label('n')).where(
        RecommendationEventV2.event_type.in_(['like', 'save']),
        RecommendationEventV2.created_at >= (db.info.get('recommendation_as_of') or datetime.now(timezone.utc)) - timedelta(days=60),
    ).group_by(RecommendationEventV2.product_id).subquery()
    # Legacy feeds store merchant category paths, not just canonical labels.
    # Resolve the small category vocabulary first, without a recency cutoff.
    base_conditions = [
        *fresh_stock_conditions(), Product.is_deleted_from_feed == 0,
        Product.is_active == 1, Product.is_available == 1,
        Product.gender_target == fit.gender_target,
        Product.price.between(300, min(25000, int(fit.budget_max))),
        func.lower(Product.source).notin_(EXCLUDED_SOURCES),
    ]
    if source:
        base_conditions.append(Product.source == source)
    if filters and filters.active:
        base_conditions.extend(filters.sql_conditions())
    if probe_roles and not _required_roles_available(db, required_roles=probe_roles,
            conditions=base_conditions, hidden=hidden, hidden_keys=keys, fit=fit, session=session,
            rules=rules, filters=filters, context=context):
        return []
    vocabulary = db.execute(select(Product.category, Product.category_name).where(*base_conditions).distinct()).all()
    category_values = {category: (set(), set()) for category in CATEGORIES}
    for raw_category, raw_name in vocabulary:
        for i, raw in enumerate((raw_category, raw_name)):
            normalized = normalize_category(raw)
            if raw and normalized in category_values:
                category_values[normalized][i].add(raw)
    pool = []
    finalist_scores = {}
    for category in CATEGORIES:
        if restrict_categories and category not in interests:
            continue
        if not any(category_values[category]):
            continue
        conditions = [*base_conditions,
                      or_(Product.category.in_(category_values[category][0]), Product.category_name.in_(category_values[category][1]))]
        if hidden: conditions.append(Product.id.notin_(hidden))
        query = select(Product).outerjoin(popularity, Product.id == popularity.c.pid).where(*conditions).order_by(
            affinity_order.desc(), func.coalesce(popularity.c.n, 0).desc(), Product.id)
        taken = 0
        best = []
        # Close the thin cursor even when the nonsemantic category is full.
        with closing(iter_size_candidates(db, query, fit)) as candidates:
            for product in candidates:
                if not product_ok(product, fit, session, rules=rules): continue
                if restrict_categories and not (features(product)['category'] & interests): continue
                if filters and filters.active and not filters.matches(product): continue
                if context is not None and not context.allows(product): continue
                # Rejected garments never reserve an identity. Avoid URL/image
                # normalization until all admission gates have passed.
                identity = product_identity_keys(product)
                if keys & identity: continue
                keys.update(identity)
                if semantic_ranking or context is not None:
                    # Score before the category cutoff. A matching fabric/fit beyond
                    # the first 50 IDs must be able to enter the candidate pool.
                    entry = (scorer.score(product), product.id, product)
                    if len(best) < 50: heapq.heappush(best, entry)
                    elif entry[:2] > best[0][:2]: heapq.heapreplace(best, entry)
                else:
                    pool.append(product)
                taken += 1
                if taken >= 50 and not (semantic_ranking or context is not None): break
        for score, _, product in best:
            pool.append(product)
            finalist_scores[product] = score
    # At most 50 finalists per category; do not reparse early winners after
    # the request's bounded scorer cache has evicted them during the full scan.
    pool.sort(key=lambda p: (-finalist_scores[p] if p in finalist_scores else -scorer.score(p), p.id))
    return pool


def _sequence_capacity(capacities, *, last_category=None, last_run=0):
    """Maximum suffix length with at most two consecutive cards of a category.

    Other categories provide separators, each allowing two more cards of a
    dominant category. An existing tail consumes part of its first slot.
    """
    total = sum(capacities.values())
    maximum = total
    for category, count in capacities.items():
        slots = 2 * (total - count + 1) - (last_run if category == last_category else 0)
        maximum = min(maximum, total - max(0, count - slots))
    return maximum


def diverse_products(products, limit, *, exploration=False, allow_category_fallback=False,
                     exploration_offset=0, selection_sources=None):
    remaining = list(products)
    result = []
    categories, brands = Counter(), Counter()
    category_cap = max(3, ceil(limit / 4))
    remaining_categories = Counter(normalize_category(p.category or '') for p in remaining)
    capacities = {category: min(count, category_cap) for category, count in remaining_categories.items()}
    target = limit if allow_category_fallback else min(limit, _sequence_capacity(capacities))
    brand_cap = max(4, ceil(limit / 3), ceil(limit / max(1, len({p.brand for p in products}))))
    while remaining and len(result) < target:
        # Every fifth card comes from a softer-ranked alternative in the same
        # allowed categories. Quality constraints have already been applied.
        indexes = list(range(len(remaining)))
        explore = exploration and (exploration_offset + len(result)) % 5 == 4
        midpoint = len(indexes)//2
        if explore:
            indexes = indexes[midpoint:] + indexes[:midpoint]
        chosen = None
        # Brand variety is a preference, not an availability constraint. A
        # dominant affordable brand must not strand onboarding below 15 cards
        # when enough size/budget-qualified garments exist in other categories.
        constraints = [(True, True), (True, False)]
        if allow_category_fallback:
            # A filtered category may legitimately fill the whole page. Prefer
            # variety while possible, then fill from the already eligible pool.
            constraints += [(False, True), (False, False)]
        for enforce_category_cap, enforce_brand_cap in constraints:
            for i in indexes:
                p = remaining[i]; cat = normalize_category(p.category or '')
                if enforce_category_cap and categories[cat] >= category_cap: continue
                if enforce_brand_cap and brands[p.brand] >= brand_cap: continue
                if enforce_category_cap and len(result) >= 2 and all(normalize_category(q.category or '') == cat for q in result[-2:]): continue
                if enforce_category_cap and not allow_category_fallback:
                    # Do not spend the last separator while a complete diverse
                    # slate is still possible. This retains both existing caps;
                    # a genuinely sparse pool keeps its achievable short page.
                    after = {category: min(count - int(category == cat),
                        category_cap - categories[category] - int(category == cat))
                        for category, count in remaining_categories.items()}
                    tail = 2 if result and normalize_category(result[-1].category or '') == cat else 1
                    if _sequence_capacity(after, last_category=cat, last_run=tail) < target - len(result) - 1:
                        continue
                chosen = i; break
            if chosen is not None: break
        if chosen is None: break
        if selection_sources is not None:
            # A sparse/diversity-constrained pool may only offer its strongest
            # remaining choice. Do not label that fallback as softer discovery.
            selection_sources[remaining[chosen].id] = 'soft_exploration' if explore and len(remaining) > 1 and chosen >= midpoint else 'taste_match'
        p = remaining.pop(chosen)
        cat = normalize_category(p.category or '')
        remaining_categories[cat] -= 1
        result.append(p); categories[cat] += 1; brands[p.brand] += 1
    return result


@profile_input_writer('user')
def configure(db, user, *, age_group, clothing_size, shoe_size, styles, price_segment,
              clothing_size_system=None, shoe_size_system=None, height=None, sizes=None, gender_target=None):
    from .onboarding_service import apply_step1, apply_step3
    if gender_target not in {'menswear', 'womenswear', 'unisex'}:
        raise HTTPException(status_code=400, detail='Явно выберите гардероб.')
    if gender_target != 'womenswear':
        raise HTTPException(status_code=400, detail='Новый подбор пока доступен для женского гардероба.')
    lock_user(db, user)
    profile, fit, taste = _preferences(db, user)
    styles = list(dict.fromkeys(styles))
    if len(styles) not in (2, 3) or not set(styles) <= STYLES:
        raise HTTPException(status_code=400, detail='Выберите два или три стиля.')
    if price_segment not in SEGMENT_BUDGETS:
        raise HTTPException(status_code=400, detail='Выберите бюджет: economy, mass или mid.')
    if age_group not in {'18-24', '25-34', '35-44', '45+'}:
        raise HTTPException(status_code=400, detail='Укажите возрастную группу.')
    size = normalize_size_token(clothing_size)
    shoe = normalize_size_token(shoe_size)
    if size not in {'XXS','XS','S','M','L','XL','XXL','XXXL','40','42','44','46','48','50','52','54','56'} or shoe not in {str(n) for n in range(34, 43)}:
        raise HTTPException(status_code=400, detail='Укажите размер одежды и обуви.')
    session = db.get(ActivationSession, user.id)
    if session and session.completed_at:
        raise HTTPException(status_code=409, detail='Первая настройка завершена. Измените параметры в профиле.')
    if session is None:
        session = ActivationSession(user_id=user.id)
        db.add(session)
    # Changed preferences invalidate the deck; reactions still inform the taste
    # model, but the new configuration needs its own reviewed selection.
    systems={'clothing_size_system':clothing_size_system,'shoe_size_system':shoe_size_system}
    same = (fit is not None and fit.gender_target == gender_target and fit.clothing_size == size and session.shoe_size == shoe
            and all((fit.style_constraints or {}).get(k)==v for k,v in systems.items())
            and set(profile.style_preferences or []) == set(styles) and profile.price_segment == price_segment)
    apply_step1(db, user=user, profile=profile, gender=gender_target, age_group=age_group)
    apply_step3(db, user=user, profile=profile, style_preferences=styles, price_segment=price_segment)
    fit = db.scalar(select(FitProfile).where(FitProfile.user_id == user.id))
    fit.clothing_size = size
    from .fit_size_profile import submitted_sizes, update_size_profile
    update_size_profile(fit, sizes=submitted_sizes(clothing_size=size, clothing_system=clothing_size_system,
        shoe_size=shoe, shoe_system=shoe_size_system, overrides=sizes), height_confirmed=height is not None)
    if height is not None:
        fit.height_cm = height
        user.height_cm = height
    fit.style_constraints = {**(fit.style_constraints or {}), **systems}
    # Old onboarding inferred categories from tags. The first feed must use
    # categories the person actually liked, then explicit profile interests.
    fit.interest_categories = []
    session.shoe_size = shoe
    if not same:
        session.swipe_product_ids = []
        session.swipes_json = {}
    session.updated_at = datetime.now(timezone.utc)
    db.flush()
    return session


def _require_activation_gender(fit):
    if not fit or fit.gender_target != 'womenswear':
        raise HTTPException(status_code=409, detail='Явно выберите женский гардероб в настройках.')


def swipe_deck(db, user, session):
    from ..schemas.api_product import product_to_api
    lock_user(db, user)
    profile, fit, _ = _preferences(db, user)
    _require_activation_gender(fit)
    rules = load_rules_by_source_id(db)
    ids = list(session.swipe_product_ids or [])
    rated = session.swipes_json or {}
    products = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_(ids)))} if ids else {}
    remaining = [products[pid] for pid in ids if pid not in rated and pid in products and product_ok(products[pid], fit, session, rules=rules)]
    count = rated_count(session)
    target = 30 if count >= MIN_SWIPES and not can_complete(session) else SWIPE_TARGET
    need = max(0, target - count - len(remaining))
    if need:
        pool = candidate_pool(db, user, session, exclude_ids=ids)
        extra = diverse_products(pool, need)
        remaining.extend(extra)
        session.swipe_product_ids = ids + [p.id for p in extra]
    return {'products': [product_to_api(p) for p in remaining], 'rated': count, 'target': target,
            'minimum': MIN_SWIPES, 'can_continue': can_complete(session)}


@profile_input_writer('user')
def swipe_feedback(db, user, session, product_id, reaction):
    from ..schemas.product_event import EventRequest
    from .product_event_service import record_product_event
    lock_user(db, user)
    fit = db.scalar(select(FitProfile).where(FitProfile.user_id == user.id))
    _require_activation_gender(fit)
    if session.completed_at:
        raise HTTPException(status_code=409, detail='Настройка вкуса уже завершена.')
    if reaction not in {'like', 'dislike', 'unavailable'} or product_id not in (session.swipe_product_ids or []):
        raise HTTPException(status_code=400, detail='Оцените вещь из текущей подборки Style Swipe.')
    ratings = dict(session.swipes_json or {})
    if product_id not in ratings:
        if reaction != 'unavailable':
            product = db.get(Product, product_id)
            if product is None or not product_ok(product, fit, session, rules=load_rules_by_source_id(db)):
                raise HTTPException(status_code=409, detail='Карточка больше не подходит вашим параметрам. Обновите подборку.')
            record_product_event(db, user, EventRequest(product_id=product_id, event_type=reaction,
                meta={'surface':'style_swipe', 'activation_version':1}), commit=False)
        ratings[product_id] = reaction
        session.swipes_json = ratings
        session.updated_at = datetime.now(timezone.utc)
        db.flush()
    count = rated_count(session)
    return {'rated': count, 'minimum': MIN_SWIPES, 'target': SWIPE_TARGET, 'can_continue': can_complete(session)}


def _matched_confirmed_size(product, fit):
    """Return exact available merchant evidence for this garment's Fit slot."""
    from .fit_size_profile import requested_size_for_product
    from .catalog.size_resolver import resolve_size, resolve_product_sizes
    from .catalog.stock_freshness import availability_snapshot
    from .personal_size_gate import evaluate_personal_size
    if product is None:
        return None
    if not evaluate_personal_size(product, fit).eligible:
        return None
    requested, system = requested_size_for_product(fit, product)
    if not requested:
        return None
    wanted = resolve_size(requested, size_system=system,
        category=product.category or product.category_name or '')
    if not wanted.known:
        return None
    if not availability_snapshot(product).available:
        return None
    return next((size for size in resolve_product_sizes(product)
        if size.known and size.availability == 'available'
        and size.normalized_size == wanted.normalized_size
        and size.size_system == wanted.size_system), None)


def reasons_for(product, profile, fit, taste, session):
    f = features(product)
    matches = sorted(f['style'] & set(getattr(profile, 'style_preferences', None) or []))
    reason = STYLE_LABELS[matches[0]] + ' — вы выбрали этот стиль' if matches else ''
    if any(float((getattr(taste, 'category_weights', None) or {}).get(c, 0)) > 0 for c in f['category']):
        reason = 'Похоже на категории, которые вам понравились'
    facts = []
    matched = _matched_confirmed_size(product, fit)
    if matched is not None:
        facts.append(f'Есть размер {matched.normalized_size} {matched.size_system}')
    maximum = getattr(fit, 'budget_max', None)
    if maximum and 0 < as_rubles(product.price) <= as_rubles(maximum):
        facts.append(f'в бюджете до {maximum} ₽')
    return ([reason] if reason else []) + ([' · '.join(facts)] if facts else [])


def _wow_explanation(products, profile, fit):
    """Describe current slot evidence; never reuse general legacy sizes."""
    styles = set().union(*(features(p)['style'] for p in products if p is not None))
    matches = styles & set(getattr(profile, 'style_preferences', None) or [])
    parts = [STYLE_LABELS[sorted(matches)[0]]] if matches else []
    maximum = getattr(fit, 'budget_max', None)
    if products and all(p is not None for p in products) and maximum:
        total = sum((as_rubles(p.price) for p in products), as_rubles(0))
        if 0 < total <= as_rubles(maximum):
            parts.append(f'до {maximum} ₽')
    if products and all(_matched_confirmed_size(p, fit) is not None for p in products):
        parts.append('Размеры указаны в наличии')
    return ' · '.join(parts)


def personalized_feed(db, user, session, *, limit=20, exclude_ids=(), filters=None, source=None, scenario='daily', context=None, publish=True):
    from .recommendation_engine import ScoredProduct
    profile, fit, taste = _preferences(db, user)
    from .mie_scoring import load_user_taste_feature_map
    from .recommendation_context import context_for
    context = context or (context_for(scenario) if scenario != 'daily' else None)
    taste_features = load_user_taste_feature_map(db, user_id=user.id)
    scorer = AffinityScorer(profile, taste, fit=fit,
        seen_counts=load_ranking_seen_counts(db, user_id=user.id,
            as_of=db.info.get('recommendation_as_of') or datetime.now(timezone.utc)),
        taste_features=taste_features, context=context)
    pool = candidate_pool(db, user, session, exclude_ids=exclude_ids, restrict_categories=True,
                          filters=filters, context=context, taste_features=taste_features,
                          scorer=scorer, preferences=(profile, fit, taste), source=source)
    viewed = db.scalar(select(func.count()).select_from(ActivationExposure).where(ActivationExposure.user_id == user.id)) or 0
    first = viewed < 20
    cap = min(max(1, limit), 20 - viewed) if first else min(max(1, limit), 30)
    # Keep the requested 80/20 slate policy after common five-factor ranking.
    # Only already-admitted alternatives participate; small first-session pages
    # continue the schedule from actual visible exposures, not prefetch counts.
    selection_sources = {}
    chosen = diverse_products(pool, cap, exploration=True, exploration_offset=viewed,
                              allow_category_fallback=bool(filters and filters.categories),
                              selection_sources=selection_sources)
    if publish:
        issued = list(session.issued_feed_ids or [])
        session.issued_feed_ids = list(dict.fromkeys(issued + [p.id for p in chosen]))[-200:]
    rows = []
    for i, p in enumerate(chosen):
        reasons = reasons_for(p, profile, fit, taste, session)[:2]
        evaluation = scorer.evaluate(p)
        if evaluation.taste_reasons and evaluation.taste_score > .6:
            reasons = [evaluation.taste_reasons[0], *reasons[-1:]]
        rows.append(ScoredProduct(product=p, final_score=evaluation.affinity,
            breakdown=evaluation.breakdown,
            reason=reasons[0] if reasons else 'Подборка для вас', reasons=reasons,
            candidate_sources=['style_category_pool', selection_sources[p.id],
                *(['exploration_prior'] if evaluation.exploration_reasons else [])],
            algorithm_version='activation_rules_v3_mie', ranking_algorithm='first_session_feed' if first else 'segment_rules_v3_mie'))
    return rows


def record_exposure_event(db, user, product_id, event_type, *, surface=None):
    if surface == 'style_swipe':
        return
    with owner_mutation(db, user.id):
        _apply_exposure_event(db, user, product_id, event_type, surface=surface)


def _apply_exposure_event(db, user, product_id, event_type, *, surface=None):
    session = db.get(ActivationSession, user.id)
    if not session or not session.completed_at: return
    row = db.scalar(select(ActivationExposure).where(ActivationExposure.user_id == user.id, ActivationExposure.product_id == product_id))
    before = None if row is None else (row.reaction, row.saved, row.opened, row.clicked)
    if row is None:
        # Only actual feed interactions start first-session exposures.
        if surface != 'feed' or product_id not in (session.issued_feed_ids or []): return
        if event_type not in {'view','like','dislike','skip','save','open_product','affiliate_click'}: return
        count = db.scalar(select(func.count()).select_from(ActivationExposure).where(ActivationExposure.user_id == user.id)) or 0
        if count >= 20: return
        row = ActivationExposure(id=str(uuid4()), user_id=user.id, product_id=product_id, position=count+1)
        db.add(row)
    if event_type in {'like','dislike','skip'}: row.reaction = event_type
    if event_type in {'save','unsave'}: row.saved = int(event_type == 'save')
    if event_type == 'open_product': row.opened = 1
    if event_type == 'affiliate_click': row.clicked = 1
    db.flush()
    if before != (row.reaction, row.saved, row.opened, row.clicked):
        accept_personal_change(db, user.id)


def activation_metrics(db, days=7):
    since = datetime.now(timezone.utc) - timedelta(days=max(1, min(90, days)))
    cohort = list(db.scalars(select(ActivationSession).where(ActivationSession.completed_at >= since)))
    ids = [s.user_id for s in cohort]
    rows = list(db.scalars(select(ActivationExposure).where(ActivationExposure.user_id.in_(ids)))) if ids else []
    n = len(rows)
    counts = Counter(row.user_id for row in rows)
    def rate(value, denominator): return round(100 * value / denominator, 2) if denominator else None
    positive = sum(row.reaction == 'like' or bool(row.saved) for row in rows)
    negative = sum(row.reaction == 'dislike' for row in rows)
    opened = sum(bool(row.opened) for row in rows)
    clicked = sum(bool(row.clicked) for row in rows)
    reached = sum(count >= 10 for count in counts.values())
    started = len(counts)
    return {'days': days, 'completed_onboarding_users': len(ids), 'started_feed_users': started, 'unique_first20_views': n,
            'like_or_save': positive, 'dislike': negative, 'open_product': opened, 'affiliate_click': clicked,
            'reached_card10_users': reached, 'like_save_rate': rate(positive,n), 'dislike_rate': rate(negative,n),
            'open_product_rate': rate(opened,n), 'affiliate_click_rate': rate(clicked,n),
            'reached_card10_rate': rate(reached,started),
            'onboarding_to_card10_rate': rate(reached,len(ids)),
            'targets_percent': {'like_save_rate_min':25, 'dislike_rate_max':35, 'open_product_rate_min':20,
                                'affiliate_click_rate_min':3, 'reached_card10_rate_min':60},
            'basis':'Actual unique card views, excluding Style Swipe and server prefetch. Like/Save counted as a union. Card-10 reach is among users who started the feed; onboarding-to-card-10 uses all completed onboarding users in the cohort.'}


@profile_input_writer('user')
def complete_wow(db, user, session):
    from ..models import Outfit
    from ..api.outfits import visible_outfits
    from .outfit_engine_v2 import generate_outfits_v2
    lock_user(db, user)
    profile, fit, taste = _preferences(db, user)
    _require_activation_gender(fit)
    if rated_count(session) < MIN_SWIPES:
        raise HTTPException(status_code=409, detail='Оцените хотя бы 15 вещей, чтобы мы поняли ваш вкус.')
    if not can_complete(session):
        raise HTTPException(status_code=409, detail='Пока ничего не понравилось. Найдите хотя бы одну подходящую вещь или измените предпочтения.')
    if not session.completed_at:
        specs = [('daily', 'На каждый день'), ('evening', 'На выход'),
                 ('daily', 'Похоже на ваши лайки' if 'like' in (session.swipes_json or {}).values() else 'Ваш стиль')]
        snapshots = []
        used = set()
        for scenario, title in specs:
            rows = generate_outfits_v2(db, user, count=1, scenario=scenario, replace_existing=False)
            for row in rows:
                products = [db.get(Product, pid) for pid in row.items_json.values()]
                keys = set().union(*(product_identity_keys(p) for p in products))
                if used & keys:
                    db.delete(row)
                    continue
                # Only describe similarities if a positive category/style signal
                # actually matches; broad defaults alone do not prove a like.
                if title == 'Похоже на ваши лайки' and not any(
                    any(float((taste.category_weights or {}).get(cat, 0)) > 0 for cat in features(p)['category']) for p in products):
                    title = 'Ваш стиль'
                used.update(keys)
                explanation = _wow_explanation(products, profile, fit)
                snapshots.append({'id': row.id, 'title': title, 'explanation': explanation})
        session.wow_json = snapshots
        session.completed_at = datetime.now(timezone.utc)
        profile.completed_at = session.completed_at
        profile.onboarding_step = 4
        db.flush()
    result = []
    for snapshot in session.wow_json or []:
        row = db.get(Outfit, snapshot['id'])
        visible = visible_outfits(db, user, [row]) if row else []
        if visible:
            products = [db.get(Product, pid) for pid in visible[0]['items'].values()]
            # Old completed sessions retain v23 snapshots. Revalidated products
            # must not receive stale general-size/budget claims from that text.
            result.append({**visible[0], **snapshot,
                'explanation': _wow_explanation(products, profile, fit)})
    return {'completed': True, 'outfits': result, 'completed_at': session.completed_at.isoformat(),
            'recommendation_features': ['context_v1', 'dna_ranking_v1', 'personalized_search_v1'],
            'activation_version': 1, 'first_session_feed': True}
