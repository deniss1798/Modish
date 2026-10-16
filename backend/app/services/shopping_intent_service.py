"""Persist and execute bounded shopping sessions without writing User Twin."""
from datetime import datetime, timedelta, timezone
import heapq
from uuid import uuid4
from sqlalchemy import select, func

from ..models import ShoppingIntent, Product, User, FitProfile, Outfit
from ..schemas.shopping_intent import IntentSpec
from ..schemas.api_product import product_to_api
from .catalog.stock_freshness import available_product_conditions
from .intent_eligibility import intent_facts, intent_product_problem, intent_commerce_problem, constraint_problem
from .mie_scoring import load_user_taste_feature_map
from .personalization_scoring import AffinityScorer, load_ranking_seen_counts
from .product_identity import product_identity_keys
from .profile_repository import load_preferences
from .recommendation_context import context_for
from .shopping_intent_parser import parse_intent, replacement_slot
from .intent_outfits import build_intent_outfits, outfit_problem, serialize_outfit
from .intent_explanations import product_reasons
from .intent_metrics import emit
from .recommendation_budget import check_ranking_budget
from .outfit_policy import MAX_RECOMMENDED_OUTFITS, EMPTY_OUTFITS_MESSAGE

INTENT_TTL = timedelta(hours=24)
RESULT_LIMIT = 10
GENERATION_KEY = 'generation_v1'
PENDING_MESSAGE = 'Подборка ещё готовится. Повторите запрос чуть позже.'


class IntentError(ValueError):
    def __init__(self, message, status=400, *, headers=None):
        super().__init__(message)
        self.status = status
        self.headers = headers


def generation_pending(intent):
    generation = (intent.context_json or {}).get(GENERATION_KEY) or {}
    return generation.get('state') == 'pending' and generation.get('intent_version') == intent.version


def pending_error():
    return IntentError(PENDING_MESSAGE, 409, headers={'Retry-After': '2'})


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def get_intent(db, user, intent_id, *, lock=False, now=None):
    if lock:
        from .recommendation_revision import owner_mutation
        with owner_mutation(db, user.id):
            pass  # Owner -> Intent is the shared lock order; lock lasts to commit.
    query = select(ShoppingIntent).where(ShoppingIntent.id == intent_id, ShoppingIntent.user_id == user.id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    intent = db.scalar(query)
    if intent is None:
        raise IntentError('Запрос не найден', 404)
    if not intent.active or _utc(intent.expires_at) <= _utc(now or datetime.now(timezone.utc)):
        raise IntentError('Этот запрос завершён. Создайте новый.', 410)
    if generation_pending(intent):
        raise pending_error()
    return intent


def create_intent(db, user, *, text, request_id, now=None):
    # Serialize retries for this owner; database uniqueness is the final guard.
    db.scalar(select(User.id).where(User.id == user.id).with_for_update())
    existing = db.scalar(select(ShoppingIntent).where(ShoppingIntent.user_id == user.id,
        ShoppingIntent.request_id == request_id))
    if existing:
        if existing.free_text != text.strip():
            raise IntentError('Ключ запроса уже использован', 409)
        return get_intent(db, user, existing.id, now=now)
    now = now or datetime.now(timezone.utc)
    spec = parse_intent(text)
    intent = ShoppingIntent(id=str(uuid4()), user_id=user.id, request_id=request_id,
        version=1, free_text=text.strip(), constraints_json=spec.model_dump(),
        context_json={}, active=1, created_at=now, updated_at=now, expires_at=now + INTENT_TTL)
    db.add(intent)
    emit(db, user.id, 'created', intent_id=intent.id, intent_type=spec.intent_type, category_count=len(spec.requested_categories),
        has_budget=spec.budget_max is not None, parser_version=spec.parser_version)
    emit(db, user.id, 'parsed', intent_id=intent.id, unresolved_count=len(spec.unresolved))
    db.flush()
    return intent


def refine_intent(db, user, intent_id, *, text, expected_version, reference_product_id=None, reference_outfit_id=None, now=None):
    intent = get_intent(db, user, intent_id, lock=True, now=now)
    if intent.version != expected_version:
        raise IntentError('Запрос уже изменён. Обновите результат.', 409)
    slot = replacement_slot(text)
    if slot:
        if not reference_outfit_id:
            raise IntentError('Выберите образ для замены вещи')
        return replace_outfit_item(db, user, intent_id, reference_outfit_id,
            expected_version=expected_version, slot=slot)
    _, fit, _ = load_preferences(db, user)
    context = intent.context_json or {}
    price, colors = None, set()
    if reference_product_id:
        if reference_product_id not in context.get('product_ids', []):
            raise IntentError('Выберите вещь из текущих результатов')
        reference = db.get(Product, reference_product_id)
        if reference:
            price, colors = reference.price, intent_facts(reference).get('color', set())
    previous = IntentSpec.model_validate(intent.constraints_json)
    spec = parse_intent(text, previous=previous, reference_price=price, reference_colors=colors,
        default_budget=fit.budget_max if fit else None)
    intent.constraints_json = spec.model_dump()
    intent.context_json = {**context, 'history': [*context.get('history', [])[-4:], text.strip()], 'product_ids': [], 'outfits': []}
    intent.version += 1
    intent.updated_at = now or datetime.now(timezone.utc)
    emit(db, user.id, 'refined', intent_id=intent.id, version=intent.version, action='text', unresolved_count=len(spec.unresolved))
    db.flush()
    return intent


def item_results(db, user, spec, *, excluded_ids=(), stats=None):
    profile, fit, taste = load_preferences(db, user)
    if not fit or fit.gender_target not in {'womenswear', 'menswear', 'unisex'} or not fit.budget_max:
        return []
    stats = stats if stats is not None else {}
    maximum = min(fit.budget_max, spec.budget_max or fit.budget_max)
    context = context_for(spec.occasion or 'daily', budget_max=maximum)
    scorer = AffinityScorer(profile, taste, fit=fit,
        seen_counts=load_ranking_seen_counts(db, user_id=user.id,
            as_of=db.info.get('recommendation_as_of') or datetime.now(timezone.utc)),
        taste_features=load_user_taste_feature_map(db, user_id=user.id), context=context,
        preferred_colors=spec.preferred_colors, preferred_styles=spec.preferred_styles,
        requested_categories=spec.requested_categories)
    base = [Product.source != 'demo', Product.gender_target.in_([fit.gender_target, 'unisex'])]
    if spec.requested_categories: base.append(Product.category.in_(spec.requested_categories))
    if spec.excluded_categories: base.append(Product.category.not_in(spec.excluded_categories))
    stats['catalog_count'] = db.scalar(select(func.count()).select_from(Product).where(*base))
    stats['available_count'] = db.scalar(select(func.count()).select_from(Product).where(*base, *available_product_conditions()))
    query = select(Product).where(*base, *available_product_conditions(), Product.price > 0,
        Product.price <= maximum)
    if spec.requested_categories:
        query = query.where(Product.category.in_(spec.requested_categories))
    if spec.excluded_categories:
        query = query.where(Product.category.not_in(spec.excluded_categories))
    if spec.budget_min is not None:
        query = query.where(Product.price >= spec.budget_min)
    if excluded_ids:
        query = query.where(Product.id.not_in(list(excluded_ids)))
    best = []
    seen = set()
    for excluded in db.scalars(select(Product).where(Product.id.in_(list(excluded_ids)))):
        seen.update(product_identity_keys(excluded))
    # A stable first eligible ID represents overlapping offer identities.
    # Admission precedes identity/scoring; this is not a best-score alias search.
    # No arbitrary updated-at cutoff is applied to the candidate catalogue.
    for product in db.scalars(query.order_by(Product.id).execution_options(yield_per=200)):
        check_ranking_budget(db)
        problem = intent_commerce_problem(product, fit, spec)
        stats['candidate_count_after_stock_budget'] = stats.get('candidate_count_after_stock_budget', 0)+1
        if problem not in {'fit_profile_unknown', 'gender_mismatch', 'explicit_filter_mismatch'}:
            stats['size_checked'] = stats.get('size_checked', 0)+1
            if not str(problem or '').startswith('size_'):
                stats['size_passed'] = stats.get('size_passed', 0)+1
        if problem:
            continue
        facts = intent_facts(product)
        if constraint_problem(product,spec,facts):
            continue
        stats['eligible_count'] = stats.get('eligible_count', 0)+1
        keys = product_identity_keys(product)
        if keys & seen:
            continue
        seen.update(keys)
        score = scorer.score(product)
        entry = (score, product.id, product, facts)
        if len(best) < RESULT_LIMIT * 10:
            heapq.heappush(best, entry)
        elif entry[:2] > best[0][:2]:
            heapq.heapreplace(best, entry)
    result, seen = [], set()
    for _, _, product, facts in sorted(best, key=lambda x: x[:2], reverse=True):
        keys = product_identity_keys(product)
        if keys & seen:
            continue
        seen.update(keys)
        reasons = product_reasons(product, fit, spec, facts=facts,
            taste_features=scorer.taste_features, behavior=getattr(taste, 'behavior_snapshot', {}))
        result.append({'product': product_to_api(product), 'reasons': reasons[:3]})
        if len(result) == RESULT_LIMIT:
            break
    return result


def intent_response(db, user, intent, *, remember=False):
    if generation_pending(intent):
        raise pending_error()
    spec = IntentSpec.model_validate(intent.constraints_json)
    stats = {}
    context = intent.context_json or {}
    generate = remember and context.get('result_version') != intent.version
    excluded = set(context.get('excluded_product_ids', []))
    if spec.intent_type == 'outfit':
        excluded.update((context.get('replacement') or {}).get('excluded_ids', []))
    products, outfits = [], []
    if not spec.unresolved:
        if not generate and ('product_ids' in context or 'outfits' in context):
            # A GET revalidates exactly the issued snapshot. It must not rescore
            # the catalogue or drop a still-valid item when newer candidates rank higher.
            profile, fit, taste = load_preferences(db, user)
            issued_ids = set(context.get('product_ids', []))
            issued_ids.update(pid for o in context.get('outfits', []) for pid in o['items'].values())
            issued = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_(issued_ids))
                .execution_options(populate_existing=True))} if issued_ids else {}
            # An import can turn an excluded offer into an alias of an
            # unchanged issued item after publication. Replay never rescans or
            # substitutes catalogue rows, but must remove these new aliases.
            from .recommendation_publication import identity_projection
            excluded_keys = set()
            if excluded:
                with db.execute(identity_projection(excluded)) as identities:
                    for row in identities:
                        excluded_keys.update(product_identity_keys(row))
            if spec.intent_type == 'single_item':
                features = load_user_taste_feature_map(db, user_id=user.id)
                seen = set(excluded_keys)
                for pid in context.get('product_ids', []):
                    product = issued.get(pid)
                    if product is None or pid in excluded or intent_product_problem(product, fit, spec):
                        continue
                    keys = product_identity_keys(product)
                    if keys & seen:
                        continue
                    seen.update(keys)
                    products.append({'product': product_to_api(product), 'reasons': product_reasons(
                        product, fit, spec, facts=intent_facts(product), taste_features=features,
                        behavior=getattr(taste, 'behavior_snapshot', {}))[:3]})
            else:
                previous_keys = []
                for old in context.get('outfits', []):
                    group = {slot: issued.get(pid) for slot, pid in old['items'].items()}
                    if any(p is None or p.id in excluded for p in group.values()) or outfit_problem(group, fit, spec):
                        continue
                    seen = set(excluded_keys)
                    duplicate = False
                    for product in group.values():
                        keys = product_identity_keys(product)
                        if keys & seen:
                            duplicate = True
                            break
                        seen.update(keys)
                    if duplicate:
                        continue
                    if not (context.get('replacement') or {}).get('locked') and any(
                            sum(bool(product_identity_keys(p) & keys) for p in group.values()) > len(group) - 2
                            for keys in previous_keys):
                        continue
                    previous_keys.append(seen - excluded_keys)
                    outfits.append(serialize_outfit(group, fit, spec, intent_id=intent.id, version=intent.version))
                    if len(outfits) >= MAX_RECOMMENDED_OUTFITS:
                        break
        elif spec.intent_type == 'single_item':
            products = item_results(db, user, spec, excluded_ids=excluded, stats=stats)
        else:
            replacement = context.get('replacement') or {}
            locked = {slot: db.get(Product, pid) for slot, pid in replacement.get('locked', {}).items()}
            if not any(p is None for p in locked.values()):
                outfits = build_intent_outfits(db, user, spec, intent_id=intent.id, version=intent.version,
                    locked=locked, excluded_ids=[*replacement.get('excluded_ids', []), *excluded], stats=stats)
    saved_ids = set(db.scalars(select(Outfit.id).where(Outfit.user_id == user.id,
        Outfit.is_saved == 1, Outfit.id.in_([o['id'] for o in outfits])))) if outfits else set()
    for outfit in outfits:
        outfit['is_saved'] = outfit['id'] in saved_ids
    fit = db.scalar(select(FitProfile).where(FitProfile.user_id == user.id))
    if generate:
        remember_results(db, user, intent, spec, fit, products, outfits, stats)
    return response_payload(intent, spec, fit, products, outfits)


def issued_intent_response(db, user, intent):
    """HTTP reads/replays only revalidate a published snapshot; never rank."""
    context = intent.context_json or {}
    if generation_pending(intent) or context.get('result_version') != intent.version:
        raise pending_error()
    if 'product_ids' not in context and 'outfits' not in context:
        raise pending_error()
    return intent_response(db, user, intent)


def remember_results(db, user, intent, spec, fit, products, outfits, stats):
    """Persist one issued version and its metrics in the caller's transaction."""
    context = intent.context_json or {}
    if context.get('result_version') == intent.version:
        return
    intent.context_json = {**context, 'product_ids': [p['product']['id'] for p in products],
        'outfits': [{'id': o['id'], 'items': o['items']} for o in outfits], 'result_version': intent.version}
    returned = {row['product']['id'] for row in products}
    returned.update(pid for outfit in outfits for pid in outfit['items'].values())
    invalid = sum(bool(intent_product_problem(db.get(Product, pid), fit, spec)) for pid in returned)
    emit(db, user.id, 'results_generated', intent_id=intent.id, version=intent.version, intent_type=spec.intent_type,
        **stats, returned_count=len(returned), invalid_returned_count=invalid,
        outfit_count=len(outfits), empty=not (products or outfits))
    emit(db, user.id, 'availability_filtered', intent_id=intent.id, version=intent.version,
        catalog_count=stats.get('catalog_count', 0), available_count=stats.get('available_count', 0))
    emit(db, user.id, 'size_filtered', intent_id=intent.id, version=intent.version,
        size_checked=stats.get('size_checked', 0), size_passed=stats.get('size_passed', 0))
    for outfit in outfits:
        emit(db, user.id, 'outfit_generated', intent_id=intent.id, version=intent.version, outfit_id=outfit['id'])


def response_payload(intent, spec, fit, products, outfits):
    state = 'needs_clarification' if spec.unresolved else 'ready'
    return {'id': intent.id, 'version': intent.version, 'text': intent.free_text,
        'expires_at': _utc(intent.expires_at).isoformat(), 'state': state,
        'constraints': spec.model_dump(), 'products': products, 'outfits': outfits,
        'effective_budget_max': min(fit.budget_max, spec.budget_max or fit.budget_max) if fit and fit.budget_max else None,
        'message': ('Уточните запрос: ' + '; '.join(spec.unresolved)) if spec.unresolved else
            EMPTY_OUTFITS_MESSAGE if spec.intent_type == 'outfit' and not outfits else
            'В каталоге пока нет вещей с подтверждённым размером и всеми указанными условиями.' if spec.intent_type == 'single_item' and not products else None}


def _selected_outfit(db, user, intent_id, outfit_id, expected_version):
    intent = get_intent(db, user, intent_id, lock=True)
    if intent.version != expected_version:
        raise IntentError('Запрос уже изменён. Обновите результат.', 409)
    selected = next((o for o in (intent.context_json or {}).get('outfits', []) if o['id'] == outfit_id), None)
    if selected is None:
        raise IntentError('Выберите образ из текущих результатов', 404)
    products = {slot: db.get(Product, pid) for slot, pid in selected['items'].items()}
    fit = db.scalar(select(FitProfile).where(FitProfile.user_id == user.id))
    spec = IntentSpec.model_validate(intent.constraints_json)
    if any(p is None for p in products.values()) or outfit_problem(products, fit, spec):
        raise IntentError('Цена или наличие изменились. Обновите подборку.')
    return intent, spec, products


def replace_outfit_item(db, user, intent_id, outfit_id, *, expected_version, slot):
    intent, spec, products = _selected_outfit(db, user, intent_id, outfit_id, expected_version)
    if slot not in products:
        raise IntentError('В этом образе нет выбранной части')
    locked = {key: p for key, p in products.items() if key != slot}
    excluded = list(dict.fromkeys([*(intent.context_json.get('replacement', {}).get('excluded_ids', []))[-30:], products[slot].id]))
    candidates = build_intent_outfits(db, user, spec, intent_id=intent.id, version=intent.version + 1,
        locked=locked, excluded_ids=[*intent.context_json.get('excluded_product_ids', []), *excluded])
    if not candidates:
        raise IntentError('Подходящей замены пока нет. Остальные вещи и бюджет сохранены.')
    intent.version += 1
    intent.updated_at = datetime.now(timezone.utc)
    intent.context_json = {**intent.context_json, 'replacement': {
        'locked': {key: p.id for key, p in locked.items()}, 'excluded_ids': excluded}}
    emit(db, user.id, 'outfit_item_replaced', intent_id=intent.id, version=intent.version, action=slot, outfit_id=outfit_id)
    emit(db, user.id, 'updated', intent_id=intent.id, version=intent.version, action='replace_item')
    return intent


def save_intent_outfit(db, user, intent_id, outfit_id, *, expected_version):
    intent, spec, products = _selected_outfit(db, user, intent_id, outfit_id, expected_version)
    outfit = db.get(Outfit, outfit_id)
    previously_saved = bool(outfit and outfit.is_saved)
    if outfit is None:
        fit = db.scalar(select(FitProfile).where(FitProfile.user_id == user.id))
        data = serialize_outfit(products, fit, spec, intent_id=intent.id, version=intent.version)
        outfit = Outfit(id=outfit_id, user_id=user.id, items_json=data['items'], total_price=data['total_price'],
            style_direction=spec.occasion or 'daily', reason=data['reason'], score=0, is_saved=1,
            intent_constraints_json=spec.model_dump())
        db.add(outfit)
    elif outfit.user_id != user.id:
        raise IntentError('Образ не найден', 404)
    else:
        outfit.is_saved = 1
    if not previously_saved:
        emit(db, user.id, 'outfit_saved', intent_id=intent.id, version=intent.version, outfit_id=outfit_id)
    db.flush()
    if not previously_saved:
        from .recommendation_revision import accept_personal_change
        accept_personal_change(db, user.id)
    return {'status': 'ok', 'outfit_id': outfit.id}


def feedback(db, user, intent_id, *, expected_version, product_id, reason):
    intent = get_intent(db, user, intent_id, lock=True)
    if intent.version != expected_version:
        raise IntentError('Запрос уже изменён. Обновите результат.', 409)
    context = intent.context_json or {}
    owned = set(context.get('product_ids', []))
    owned.update(pid for o in context.get('outfits', []) for pid in o['items'].values())
    if product_id not in owned:
        raise IntentError('Выберите вещь из текущих результатов', 404)
    spec = IntentSpec.model_validate(intent.constraints_json)
    emit(db, user.id, 'feedback', intent_id=intent.id, version=intent.version, reason=reason)
    if reason in {'too_expensive', 'not_for_context'}:
        emit(db, user.id, 'reason_dislike', intent_id=intent.id, version=intent.version,
            product_id=product_id, reason=reason)
    if reason == 'too_expensive':
        return refine_intent(db, user, intent.id, expected_version=expected_version, text='Слишком дорого',
            reference_product_id=product_id if spec.intent_type == 'single_item' else None)
    excluded = list(dict.fromkeys([*context.get('excluded_product_ids', []), product_id]))
    if len(excluded) > 200:
        raise IntentError('Начните новый запрос, чтобы продолжить подбор')
    # A task rejection never becomes a permanent item or style rejection.
    if reason != 'not_for_context':
        from .product_event_service import record_product_event
        from ..schemas.product_event import EventRequest
        record_product_event(db, user, EventRequest(product_id=product_id, event_type='dislike',
            meta={'reason': reason, 'surface': 'shopping_intent', 'intent_id': intent.id, 'intent_version': intent.version}), commit=False)
    replacement = context.get('replacement') or {}
    locked = {slot: pid for slot, pid in replacement.get('locked', {}).items() if pid != product_id}
    intent.context_json = {**context, 'excluded_product_ids': excluded,
        'replacement': {**replacement, 'locked': locked}}
    intent.version += 1
    intent.updated_at = datetime.now(timezone.utc)
    emit(db, user.id, 'updated', intent_id=intent.id, version=intent.version, action='feedback')
    return intent


def close_intent(db, user, intent_id, *, expected_version, outcome):
    from .recommendation_revision import owner_mutation
    with owner_mutation(db, user.id):
        pass
    intent=db.scalar(select(ShoppingIntent).where(ShoppingIntent.id==intent_id,
        ShoppingIntent.user_id==user.id).with_for_update().execution_options(populate_existing=True))
    if intent is None: raise IntentError('Запрос не найден',404)
    if intent.version!=expected_version: raise IntentError('Версия запроса уже изменилась',409)
    if not intent.active:
        if (intent.context_json or {}).get('closure_outcome')==outcome:
            return {'status':'ok','outcome':outcome}
        raise IntentError('Этот запрос уже завершён',410)
    if _utc(intent.expires_at)<=datetime.now(timezone.utc):
        raise IntentError('Этот запрос завершён. Создайте новый.',410)
    intent.active=0
    intent.updated_at=datetime.now(timezone.utc)
    intent.context_json={**(intent.context_json or {}),'closure_outcome':outcome}
    emit(db,user.id,outcome,intent_id=intent.id,version=intent.version)
    db.flush()
    return {'status':'ok','outcome':outcome}
