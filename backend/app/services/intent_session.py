"""Single-item create: durable claim, read-only ranking, short publication.

Outfit creation keeps its existing protected implementation. Intent constraints
and exclusions retain their own policy; feed rules are not added here.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select, text as sql_text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from ..models import FitProfile, Product, ShoppingIntent, User
from ..schemas.api_product import product_to_api
from ..schemas.shopping_intent import IntentSpec
from . import shopping_intent_service as service
from .activation_service import require_warm_feed
from .intent_eligibility import intent_product_problem
from .product_identity import product_identity_keys
from .profile_repository import ensure_style_profile, ensure_taste_profile
from .recommendation_publication import identity_projection, identity_signature, lock_catalogue, product_evidence
from .recommendation_revision import RecommendationOwnerMissing, RecommendationSnapshotChanged, owner_mutation, profile_input_signature
from .shopping_intent_service import item_results

PENDING_LEASE_SECONDS = 120
CONFLICT_MESSAGE = 'Параметры или каталог изменились во время подбора. Повторите запрос.'
_RETRYABLE_SQLSTATES = {'55P03', '57014', '40001', '40P01'}


@dataclass(frozen=True)
class IntentClaim:
    intent_id: str
    version: int
    generation: int
    claim_id: str


@dataclass(frozen=True)
class IntentAdmission:
    intent_id: str
    claim: IntentClaim | None = None
    response: dict | None = None


@dataclass(frozen=True)
class IntentFinalist:
    product_id: str
    source_id: str | None
    evidence: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class IntentProposal:
    claim: IntentClaim
    revision: int
    profile_signature: str
    constraints: str
    finalists: tuple[IntentFinalist, ...]
    stats: tuple[tuple[str, int], ...]
    excluded_ids: tuple[str, ...]
    excluded_identity_signature: str


def _now():
    return datetime.now(timezone.utc)


def _generation(intent):
    value = (intent.context_json or {}).get(service.GENERATION_KEY)
    return value if isinstance(value, dict) else {}


def _constraints(intent):
    return json.dumps(intent.constraints_json, sort_keys=True, ensure_ascii=True, separators=(',', ':'))


def _active(intent):
    if intent is None:
        raise service.IntentError('Запрос не найден', 404)
    if not intent.active or service._utc(intent.expires_at) <= _now():
        raise service.IntentError('Этот запрос завершён. Создайте новый.', 410)


def _same_claim(intent, claim):
    generation = _generation(intent)
    return (intent.version == claim.version and generation.get('intent_version') == claim.version
        and generation.get('state') == 'pending' and generation.get('generation') == claim.generation
        and generation.get('claim_id') == claim.claim_id)


def _initialize(sessions, user_id):
    with sessions() as db:
        user = db.get(User, user_id)
        if user is None:
            raise RecommendationOwnerMissing()
        # Legacy warm admission/history is evaluated outside the owner lock.
        require_warm_feed(db, user)
        ensure_style_profile(db, user_id)
        ensure_taste_profile(db, user_id)
        db.commit()


def _admit(sessions, user_id, *, text, request_id):
    with sessions() as db:
        if db.get_bind().dialect.name == 'postgresql':
            db.execute(sql_text("SET LOCAL lock_timeout = '750ms'"))
        with owner_mutation(db, user_id) as mutation:
            user = mutation.owner
            intent = db.scalar(select(ShoppingIntent).where(ShoppingIntent.user_id == user_id,
                ShoppingIntent.request_id == request_id).with_for_update().execution_options(populate_existing=True))
            if intent is None:
                intent = service.create_intent(db, user, text=text, request_id=request_id, now=_now())
            elif intent.free_text != text.strip():
                raise service.IntentError('Ключ запроса уже использован', 409)
            _active(intent)
            context = intent.context_json or {}
            if context.get('result_version') == intent.version and ('product_ids' in context or 'outfits' in context):
                outcome = IntentAdmission(intent.id)
            else:
                spec = IntentSpec.model_validate(intent.constraints_json)
                if spec.intent_type == 'single_item' and db.get_bind().dialect.name == 'postgresql':
                    db.execute(sql_text("SET LOCAL statement_timeout = '2000ms'"))
                if spec.intent_type != 'single_item' or spec.unresolved:
                    # This slice does not remove the existing outfit owner lock.
                    response = service.intent_response(db, user, intent, remember=True)
                    outcome = IntentAdmission(intent.id, response=response)
                else:
                    previous = _generation(intent)
                    if service.generation_pending(intent):
                        lease = datetime.fromisoformat(previous['lease_until'])
                        if service._utc(lease) > _now():
                            raise service.pending_error()
                    generation = int(previous.get('generation') or 0) + 1
                    claim = IntentClaim(intent.id, intent.version, generation, str(uuid4()))
                    intent.context_json = {**context, service.GENERATION_KEY: {
                        'state': 'pending', 'generation': generation, 'claim_id': claim.claim_id,
                        'intent_version': intent.version,
                        'lease_until': (_now() + timedelta(seconds=PENDING_LEASE_SECONDS)).isoformat()}}
                    intent.updated_at = _now()
                    outcome = IntentAdmission(intent.id, claim)
        db.commit()
        return outcome


def _prepare(sessions, user_id, claim):
    with sessions() as db:
        if db.get_bind().dialect.name == 'postgresql':
            db.connection(execution_options={'isolation_level': 'REPEATABLE READ'})
            db.execute(sql_text('SET TRANSACTION READ ONLY'))
        db.info.update(recommendation_read_only=True, recommendation_as_of=_now())
        user = db.get(User, user_id)
        if user is None:
            raise RecommendationOwnerMissing()
        require_warm_feed(db, user)
        intent = db.scalar(select(ShoppingIntent).where(ShoppingIntent.id == claim.intent_id,
            ShoppingIntent.user_id == user_id))
        _active(intent)
        if not _same_claim(intent, claim):
            raise service.IntentError('Запрос уже изменён. Обновите результат.', 409)
        signature = profile_input_signature(db, user_id)
        spec = IntentSpec.model_validate(intent.constraints_json)
        excluded_ids = tuple(sorted(set((intent.context_json or {}).get('excluded_product_ids', []))))
        excluded_keys = set()
        if excluded_ids:
            with db.execute(identity_projection(excluded_ids)) as identities:
                for row in identities:
                    excluded_keys.update(product_identity_keys(row))
        stats = {}
        rows = item_results(db, user, spec, excluded_ids=excluded_ids, stats=stats)
        finalists = []
        for row in rows:
            product = db.get(Product, row['product']['id'])
            finalists.append(IntentFinalist(product.id, product.source_id, product_evidence(product), tuple(row['reasons'])))
        return IntentProposal(claim, user.recommendation_revision, signature, _constraints(intent),
            tuple(finalists), tuple(stats.items()), excluded_ids, identity_signature(excluded_keys))


def _publish(sessions, user_id, proposal):
    with sessions() as db:
        catalogue = lock_catalogue(db, proposal.finalists, excluded_ids=proposal.excluded_ids,
            excluded_identity_signature=proposal.excluded_identity_signature, include_source_rules=False)
        with owner_mutation(db, user_id) as mutation:
            intent = db.scalar(select(ShoppingIntent).where(ShoppingIntent.id == proposal.claim.intent_id,
                ShoppingIntent.user_id == user_id).with_for_update().execution_options(populate_existing=True))
            _active(intent)
            if not _same_claim(intent, proposal.claim):
                raise service.IntentError('Запрос уже изменён. Обновите результат.', 409)
            if (_constraints(intent) != proposal.constraints or
                    tuple(sorted(set((intent.context_json or {}).get('excluded_product_ids', [])))) != proposal.excluded_ids):
                raise service.IntentError('Запрос уже изменён. Обновите результат.', 409)
            if mutation.owner.recommendation_revision != proposal.revision or profile_input_signature(db, user_id) != proposal.profile_signature:
                raise RecommendationSnapshotChanged()
            spec = IntentSpec.model_validate(intent.constraints_json)
            fit = db.scalar(select(FitProfile).where(FitProfile.user_id == user_id))
            products = []
            for finalist in proposal.finalists:
                product = catalogue.products.get(finalist.product_id)
                if (product is None or catalogue.evidence[product.id] != finalist.evidence
                        or intent_product_problem(product, fit, spec)):
                    raise RecommendationSnapshotChanged()
                products.append({'product': product_to_api(product), 'reasons': list(finalist.reasons)})
            service.remember_results(db, mutation.owner, intent, spec, fit, products, [], dict(proposal.stats))
            generation = _generation(intent)
            intent.context_json = {**intent.context_json, service.GENERATION_KEY: {
                **generation, 'state': 'ready', 'lease_until': None}}
            intent.updated_at = _now()
            response = service.response_payload(intent, spec, fit, products, [])
        db.commit()
        return response


def _release_claim(sessions, user_id, claim):
    """Only the current unfinished claimant may make its lease recoverable."""
    with sessions() as db:
        if db.get_bind().dialect.name == 'postgresql':
            db.execute(sql_text("SET LOCAL lock_timeout = '750ms'"))
            db.execute(sql_text("SET LOCAL statement_timeout = '2000ms'"))
        with owner_mutation(db, user_id):
            intent = db.scalar(select(ShoppingIntent).where(ShoppingIntent.id == claim.intent_id,
                ShoppingIntent.user_id == user_id).with_for_update().execution_options(populate_existing=True))
            if intent is None or not _same_claim(intent, claim):
                return
            intent.context_json = {**intent.context_json, service.GENERATION_KEY: {
                **_generation(intent), 'lease_until': _now().isoformat()}}
        db.commit()


def _replay(sessions, user_id, intent_id):
    with sessions() as db:
        user = db.get(User, user_id)
        if user is None:
            raise RecommendationOwnerMissing()
        require_warm_feed(db, user)
        intent = service.get_intent(db, user, intent_id)
        return service.issued_intent_response(db, user, intent)


def build_intent_response(bind, user_id, *, text, request_id):
    sessions = sessionmaker(bind=bind, autoflush=False, expire_on_commit=False)
    claim, completed = None, False
    try:
        _initialize(sessions, user_id)
        admission = _admit(sessions, user_id, text=text, request_id=request_id)
        claim = admission.claim
        if admission.response is not None:
            return admission.response
        if claim is None:
            return _replay(sessions, user_id, admission.intent_id)
        for attempt in range(2):
            try:
                proposal = _prepare(sessions, user_id, claim)
                response = _publish(sessions, user_id, proposal)
                completed = True
                return response
            except RecommendationSnapshotChanged:
                pass
            except DBAPIError as error:
                if getattr(error.orig, 'sqlstate', None) not in _RETRYABLE_SQLSTATES:
                    raise
            if attempt == 1:
                raise service.IntentError(CONFLICT_MESSAGE, 409)
    except service.IntentError as error:
        raise HTTPException(status_code=error.status, detail=str(error), headers=error.headers) from error
    except RecommendationOwnerMissing as error:
        raise HTTPException(status_code=401, detail='Войдите снова, чтобы продолжить.') from error
    except DBAPIError as error:
        if getattr(error.orig, 'sqlstate', None) in _RETRYABLE_SQLSTATES:
            raise HTTPException(status_code=409, detail=CONFLICT_MESSAGE) from error
        raise
    finally:
        if claim is not None and not completed:
            try:
                _release_claim(sessions, user_id, claim)
            except (DBAPIError, RecommendationOwnerMissing):
                # A failed cleanup leaves a bounded lease, never another owner.
                pass
