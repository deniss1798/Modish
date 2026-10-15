"""Atomic Intent edits and feedback without ranking under the owner lock.

An existing ready version remains untouched until its complete successor can
publish. Expected-version CAS preserves replay/conflict semantics; no mutation
lease or partially accepted edit is persisted. Permanent feedback becomes real
only when the complete successor can publish in the same transaction.
"""
from contextlib import nullcontext
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json

from fastapi import HTTPException
from sqlalchemy import select, text as sql_text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from ..models import FitProfile, Outfit, Product, ShoppingIntent, StyleProfile, TasteProfile, User, UserTasteFeature
from ..schemas.product_event import EventRequest
from ..schemas.api_product import product_to_api
from ..schemas.shopping_intent import IntentSpec
from . import shopping_intent_service as service
from .activation_service import require_warm_feed
from .feedback_preview import PreviewEvent, preview_dislike, use_feedback_preview
from .intent_eligibility import intent_facts, intent_product_problem
from .intent_outfits import build_intent_outfits, outfit_problem, serialize_outfit
from .product_identity import product_identity_keys
from .product_event_service import record_product_event
from .recommendation_budget import (RecommendationRankingTimeout, TIMEOUT_MESSAGE,
    begin_ranking, check_ranking_budget)
from .recommendation_publication import ProductEvidence, identity_projection, identity_signature, lock_catalogue, product_evidence
from .recommendation_revision import RecommendationOwnerMissing, RecommendationSnapshotChanged, owner_mutation, profile_input_signature
from .shopping_intent_parser import parse_intent, replacement_slot
from .shopping_intent_service import item_results

CONFLICT_MESSAGE = 'Параметры или каталог изменились во время подбора. Повторите запрос.'
NO_REPLACEMENT_MESSAGE = 'Подходящей замены пока нет. Остальные вещи и бюджет сохранены.'
_RETRYABLE_SQLSTATES = {'55P03', '57014', '40001', '40P01'}


@dataclass(frozen=True)
class MutationRequest:
    operation: str
    expected_version: int
    text: str | None = None
    reference_product_id: str | None = None
    reference_outfit_id: str | None = None
    outfit_id: str | None = None
    slot: str | None = None
    product_id: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class FeedbackPlan:
    product_id: str
    reason: str | None
    occurred_at: datetime
    event: PreviewEvent | None = None


@dataclass(frozen=True)
class MutationPlan:
    intent_id: str
    base_version: int
    previous_constraints: str
    previous_context: str
    constraints: str
    context: str
    action: str
    source_outfit_id: str | None = None
    slot: str | None = None
    selected_items: tuple[tuple[str, str], ...] = ()
    locked_items: tuple[tuple[str, str], ...] = ()
    dependencies: tuple[ProductEvidence, ...] = ()
    feedback: FeedbackPlan | None = None


@dataclass(frozen=True)
class ItemFinalist:
    product_id: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class MutationProposal:
    plan: MutationPlan
    revision: int
    profile_signature: str
    evidence: tuple[ProductEvidence, ...]
    products: tuple[ItemFinalist, ...]
    outfits: tuple[tuple[tuple[str, str], ...], ...]
    stats: tuple[tuple[str, int], ...]
    excluded_ids: tuple[str, ...]
    excluded_identity_signature: str


def _now():
    return datetime.now(timezone.utc)


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':'))


def _evidence(product, product_id=None):
    return (ProductEvidence(product.id, product.source_id, product_evidence(product)) if product is not None
        else ProductEvidence(product_id, None, ''))


def _initialize(sessions, user_id):
    with sessions() as db:
        user = db.get(User, user_id)
        if user is None:
            raise RecommendationOwnerMissing()
        require_warm_feed(db, user)
        # A failed edit must not commit even a hidden profile initialization.
        # Published Intents have these inputs; an incomplete legacy graph needs
        # explicit profile repair before a read-only mutation can rank it.
        for model in (StyleProfile, TasteProfile):
            if db.scalar(select(model.id).where(model.user_id == user_id)) is None:
                raise service.IntentError('Профиль уже изменился. Откройте текущий профиль.', 409)


def _replace_plan(db, intent, fit, *, outfit_id, slot):
    context = intent.context_json or {}
    spec = IntentSpec.model_validate(intent.constraints_json)
    selected = next((row for row in context.get('outfits', []) if row['id'] == outfit_id), None)
    if selected is None:
        raise service.IntentError('Выберите образ из текущих результатов', 404)
    products = {key: db.get(Product, product_id) for key, product_id in selected['items'].items()}
    if any(product is None for product in products.values()) or outfit_problem(products, fit, spec):
        raise service.IntentError('Цена или наличие изменились. Обновите подборку.')
    if slot not in products:
        raise service.IntentError('В этом образе нет выбранной части')
    locked = {key: product.id for key, product in products.items() if key != slot}
    excluded = list(dict.fromkeys([
        *(context.get('replacement', {}).get('excluded_ids', []))[-30:], products[slot].id]))
    proposed = {**context, 'replacement': {'locked': locked, 'excluded_ids': excluded}}
    return MutationPlan(intent.id, intent.version, _json(intent.constraints_json), _json(context),
        _json(spec.model_dump()), _json(proposed), 'replace', outfit_id, slot,
        tuple(sorted(selected['items'].items())), tuple(sorted(locked.items())),
        tuple(_evidence(product) for product in products.values()))


def _feedback_plan(db, intent, fit, request, *, as_of):
    context = intent.context_json or {}
    owned = set(context.get('product_ids', []))
    owned.update(product_id for row in context.get('outfits', []) for product_id in row['items'].values())
    if request.product_id not in owned:
        raise service.IntentError('Выберите вещь из текущих результатов', 404)
    product = db.get(Product, request.product_id)
    if product is None:
        raise service.IntentError('Вещь уже недоступна. Обновите результат.', 409)
    feedback = FeedbackPlan(product.id, request.reason, as_of)
    spec = IntentSpec.model_validate(intent.constraints_json)
    if request.reason == 'too_expensive':
        plan = prepare_plan(db, intent, fit, MutationRequest('refine', request.expected_version,
            text='Слишком дорого', reference_product_id=product.id if spec.intent_type == 'single_item' else None))
        dependencies = {row.product_id: row for row in (*plan.dependencies, _evidence(product))}
        return replace(plan, feedback=feedback, dependencies=tuple(dependencies.values()))
    excluded = list(dict.fromkeys([*context.get('excluded_product_ids', []), product.id]))
    if len(excluded) > 200:
        raise service.IntentError('Начните новый запрос, чтобы продолжить подбор')
    replacement = context.get('replacement') or {}
    locked = {slot: product_id for slot, product_id in replacement.get('locked', {}).items()
        if product_id != product.id}
    proposed = {**context, 'excluded_product_ids': excluded, 'replacement': {**replacement, 'locked': locked}}
    return MutationPlan(intent.id, intent.version, _json(intent.constraints_json), _json(context),
        _json(spec.model_dump()), _json(proposed), 'feedback', dependencies=(_evidence(product),), feedback=feedback)


def prepare_plan(db, intent, fit, request, *, as_of=None):
    """Read references and derive an immutable prospective edit; never write."""
    if request.operation == 'feedback':
        return _feedback_plan(db, intent, fit, request, as_of=as_of or _now())
    if request.operation == 'replace':
        return _replace_plan(db, intent, fit, outfit_id=request.outfit_id, slot=request.slot)
    if request.operation != 'refine':
        raise ValueError('Unknown Intent mutation operation')
    slot = replacement_slot(request.text)
    if slot:
        if not request.reference_outfit_id:
            raise service.IntentError('Выберите образ для замены вещи')
        return _replace_plan(db, intent, fit, outfit_id=request.reference_outfit_id, slot=slot)
    context = intent.context_json or {}
    price, colors, dependencies = None, set(), ()
    if request.reference_product_id:
        if request.reference_product_id not in context.get('product_ids', []):
            raise service.IntentError('Выберите вещь из текущих результатов')
        reference = db.get(Product, request.reference_product_id)
        if reference is None:
            raise service.IntentError('Вещь уже недоступна. Обновите результат.', 409)
        dependencies = (_evidence(reference),)
        price, colors = reference.price, intent_facts(reference).get('color', set())
    previous = IntentSpec.model_validate(intent.constraints_json)
    spec = parse_intent(request.text, previous=previous, reference_price=price, reference_colors=colors,
        default_budget=fit.budget_max if fit else None)
    proposed = {**context, 'history': [*context.get('history', [])[-4:], request.text.strip()],
        'product_ids': [], 'outfits': []}
    return MutationPlan(intent.id, intent.version, _json(intent.constraints_json), _json(context),
        _json(spec.model_dump()), _json(proposed), 'refine', dependencies=dependencies)


def _exclusions(plan, spec):
    context = json.loads(plan.context)
    excluded = set(context.get('excluded_product_ids', []))
    if spec.intent_type == 'outfit':
        excluded.update((context.get('replacement') or {}).get('excluded_ids', []))
    return tuple(sorted(excluded))


def _prepare(sessions, user_id, intent_id, request):
    with sessions() as db:
        if db.get_bind().dialect.name == 'postgresql':
            db.connection(execution_options={'isolation_level': 'REPEATABLE READ'})
            db.execute(sql_text('SET TRANSACTION READ ONLY'))
        db.info.update(recommendation_read_only=True, recommendation_as_of=_now())
        begin_ranking(db)
        user = db.get(User, user_id)
        if user is None:
            raise RecommendationOwnerMissing()
        require_warm_feed(db, user)
        intent = service.get_intent(db, user, intent_id, now=db.info['recommendation_as_of'])
        if intent.version != request.expected_version:
            raise service.IntentError('Запрос уже изменён. Обновите результат.', 409)
        signature = profile_input_signature(db, user_id)
        fit = db.scalar(select(FitProfile).where(FitProfile.user_id == user_id))
        plan = prepare_plan(db, intent, fit, request, as_of=db.info['recommendation_as_of'])
        spec = IntentSpec.model_validate_json(plan.constraints)
        excluded_ids = _exclusions(plan, spec)
        excluded_keys = set()
        if excluded_ids:
            with db.execute(identity_projection(excluded_ids)) as identities:
                for row in identities:
                    excluded_keys.update(product_identity_keys(row))
        stats = {}
        evidence = {row.product_id: row for row in plan.dependencies}
        preview = None
        if plan.feedback is not None and plan.feedback.reason not in {'too_expensive', 'not_for_context'}:
            taste = db.scalar(select(TasteProfile).where(TasteProfile.user_id == user_id))
            if taste is None:
                raise RecommendationSnapshotChanged()
            preview = preview_dislike(taste,
                db.scalars(select(UserTasteFeature).where(UserTasteFeature.user_id == user_id)),
                db.get(Product, plan.feedback.product_id), user_id=user_id,
                meta={'reason': plan.feedback.reason, 'surface': 'shopping_intent',
                    'intent_id': intent.id, 'intent_version': intent.version}, occurred_at=plan.feedback.occurred_at)
            plan = replace(plan, feedback=replace(plan.feedback, event=preview.events[0]))
        with use_feedback_preview(db, preview) if preview is not None else nullcontext():
            products, outfits = _rank(db, user, plan, spec, excluded_ids, stats, evidence)
        check_ranking_budget(db)
        return MutationProposal(plan, user.recommendation_revision, signature,
            tuple(evidence[product_id] for product_id in sorted(evidence)), tuple(products), tuple(outfits),
            tuple(stats.items()), excluded_ids, identity_signature(excluded_keys))


def _rank(db, user, plan, spec, excluded_ids, stats, evidence):
    products, outfits = [], []
    if not spec.unresolved:
        if spec.intent_type == 'single_item':
            rows = item_results(db, user, spec, excluded_ids=excluded_ids, stats=stats)
            for row in rows:
                product = db.get(Product, row['product']['id'])
                evidence[product.id] = _evidence(product)
                products.append(ItemFinalist(product.id, tuple(row['reasons'])))
        else:
            locked = {slot: db.get(Product, product_id) for slot, product_id in plan.locked_items}
            if not plan.locked_items:
                # Text refinement preserves the existing replacement locks.
                replacement = json.loads(plan.context).get('replacement') or {}
                locked = {slot: db.get(Product, product_id) for slot, product_id in replacement.get('locked', {}).items()}
                for product_id in replacement.get('locked', {}).values():
                    evidence[product_id] = _evidence(db.get(Product, product_id), product_id)
            rows = ([] if any(product is None for product in locked.values()) else
                build_intent_outfits(db, user, spec, intent_id=plan.intent_id, version=plan.base_version + 1,
                    locked=locked, excluded_ids=excluded_ids, stats=stats))
            if plan.action == 'replace' and not rows:
                raise service.IntentError(NO_REPLACEMENT_MESSAGE)
            for row in rows:
                outfits.append(tuple(sorted(row['items'].items())))
                for product_id in row['items'].values():
                    evidence[product_id] = _evidence(db.get(Product, product_id))
    return products, outfits


def _publish(sessions, user_id, proposal):
    with sessions() as db:
        catalogue = lock_catalogue(db, proposal.evidence, excluded_ids=proposal.excluded_ids,
            excluded_identity_signature=proposal.excluded_identity_signature, include_source_rules=False)
        with owner_mutation(db, user_id) as mutation:
            plan = proposal.plan
            intent = db.scalar(select(ShoppingIntent).where(ShoppingIntent.id == plan.intent_id,
                ShoppingIntent.user_id == user_id).with_for_update().execution_options(populate_existing=True))
            if intent is None:
                raise service.IntentError('Запрос не найден', 404)
            if not intent.active or service._utc(intent.expires_at) <= _now():
                raise service.IntentError('Этот запрос завершён. Создайте новый.', 410)
            if (intent.version != plan.base_version or _json(intent.constraints_json) != plan.previous_constraints
                    or _json(intent.context_json or {}) != plan.previous_context):
                raise service.IntentError('Запрос уже изменён. Обновите результат.', 409)
            if service.generation_pending(intent):
                raise service.pending_error()
            if (mutation.owner.recommendation_revision != proposal.revision
                    or profile_input_signature(db, user_id) != proposal.profile_signature):
                raise RecommendationSnapshotChanged()
            for expected in proposal.evidence:
                current = catalogue.products.get(expected.product_id)
                if (current is None and expected.evidence) or (
                        current is not None and catalogue.evidence[current.id] != expected.evidence):
                    raise RecommendationSnapshotChanged()
            fit = db.scalar(select(FitProfile).where(FitProfile.user_id == user_id))
            spec = IntentSpec.model_validate_json(plan.constraints)
            if plan.selected_items:
                previous = {slot: catalogue.products.get(product_id) for slot, product_id in plan.selected_items}
                if any(product is None for product in previous.values()) or outfit_problem(
                        previous, fit, IntentSpec.model_validate_json(plan.previous_constraints)):
                    raise RecommendationSnapshotChanged()
            products, outfits = [], []
            for row in proposal.products:
                product = catalogue.products.get(row.product_id)
                if product is None or intent_product_problem(product, fit, spec):
                    raise RecommendationSnapshotChanged()
                products.append({'product': product_to_api(product), 'reasons': list(row.reasons)})
            for items in proposal.outfits:
                group = {slot: catalogue.products.get(product_id) for slot, product_id in items}
                if any(product is None for product in group.values()) or outfit_problem(group, fit, spec):
                    raise RecommendationSnapshotChanged()
                outfits.append(serialize_outfit(group, fit, spec, intent_id=intent.id, version=plan.base_version + 1))
            saved = set(db.scalars(select(Outfit.id).where(Outfit.user_id == user_id,
                Outfit.is_saved == 1, Outfit.id.in_([row['id'] for row in outfits])))) if outfits else set()
            for outfit in outfits:
                outfit['is_saved'] = outfit['id'] in saved
            if plan.feedback is not None:
                feedback = plan.feedback
                service.emit(db, user_id, 'feedback', intent_id=intent.id, version=intent.version, reason=feedback.reason)
                if feedback.reason in {'too_expensive', 'not_for_context'}:
                    service.emit(db, user_id, 'reason_dislike', intent_id=intent.id, version=intent.version,
                        product_id=feedback.product_id, reason=feedback.reason)
                else:
                    if feedback.event is None:
                        raise RuntimeError('Permanent feedback requires a prepared event')
                    record_product_event(db, mutation.owner, EventRequest(product_id=feedback.product_id,
                        event_type='dislike', meta=feedback.event.metadata()), commit=False,
                        occurred_at=feedback.occurred_at)
            intent.version = plan.base_version + 1
            intent.constraints_json = json.loads(plan.constraints)
            intent.context_json = json.loads(plan.context)
            intent.updated_at = plan.feedback.occurred_at if plan.feedback is not None else _now()
            if plan.action == 'replace':
                service.emit(db, user_id, 'outfit_item_replaced', intent_id=intent.id, version=intent.version,
                    action=plan.slot, outfit_id=plan.source_outfit_id)
                service.emit(db, user_id, 'updated', intent_id=intent.id, version=intent.version, action='replace_item')
            elif plan.action == 'refine':
                service.emit(db, user_id, 'refined', intent_id=intent.id, version=intent.version,
                    action='text', unresolved_count=len(spec.unresolved))
            else:
                service.emit(db, user_id, 'updated', intent_id=intent.id, version=intent.version, action='feedback')
            service.remember_results(db, mutation.owner, intent, spec, fit, products, outfits, dict(proposal.stats))
            response = service.response_payload(intent, spec, fit, products, outfits)
        db.commit()
        return response


def build_mutation_response(bind, user_id, intent_id, *, operation, expected_version, text=None,
        reference_product_id=None, reference_outfit_id=None, outfit_id=None, slot=None, product_id=None, reason=None):
    request = MutationRequest(operation, expected_version, text, reference_product_id,
        reference_outfit_id, outfit_id, slot, product_id, reason)
    sessions = sessionmaker(bind=bind, autoflush=False, expire_on_commit=False)
    try:
        _initialize(sessions, user_id)
        for attempt in range(2):
            try:
                try:
                    proposal = _prepare(sessions, user_id, intent_id, request)
                except DBAPIError as error:
                    if getattr(error.orig, 'sqlstate', None) == '57014':
                        raise RecommendationRankingTimeout() from error
                    raise
                return _publish(sessions, user_id, proposal)
            except RecommendationSnapshotChanged:
                pass
            except DBAPIError as error:
                if getattr(error.orig, 'sqlstate', None) not in _RETRYABLE_SQLSTATES:
                    raise
            if attempt == 1:
                raise service.IntentError(CONFLICT_MESSAGE, 409)
    except service.IntentError as error:
        raise HTTPException(status_code=error.status, detail=str(error), headers=error.headers) from error
    except RecommendationRankingTimeout as error:
        raise HTTPException(status_code=409, detail=TIMEOUT_MESSAGE) from error
    except RecommendationOwnerMissing as error:
        raise HTTPException(status_code=401, detail='Войдите снова, чтобы продолжить.') from error
