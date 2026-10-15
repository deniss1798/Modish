"""Snapshot/rank/current-evidence publication for personalized product GETs.

These reads accept no feedback and allocate no result/cache/exposure rows.
Missing scorer profiles are initialized separately before read-only ranking.
"""
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from ..models import ActivationSession, FitProfile, Product, User
from ..money import as_rubles
from ..schemas.api_product import product_to_api
from .activation_policy import product_ok
from .candidate_retrieval_service import _fit_gender_condition
from .catalog.stock_freshness import fresh_stock_conditions
from .garment_roles import product_role
from .outfit_engine_v2 import _hard_ok, _scenario_product_ok
from .outfit_quality import is_sports_item, pair_compatible
from .product_complements import COMPLEMENT_ROLES, complementary_products
from .product_identity import product_identity_keys
from .product_search import personalized_search, search_conditions
from .profile_repository import ensure_style_profile, ensure_taste_profile
from .recommendation_budget import (RecommendationRankingTimeout, TIMEOUT_MESSAGE,
    begin_ranking, check_ranking_budget)
from .recommendation_publication import (ProductEvidence, identity_projection,
    identity_signature, lock_catalogue, product_evidence)
from .recommendation_revision import (RecommendationOwnerMissing, RecommendationSnapshotChanged,
    owner_mutation, profile_input_signature)

CONFLICT_MESSAGE = 'Параметры или каталог изменились во время подбора. Повторите запрос.'
_RETRYABLE_SQLSTATES = {'55P03', '57014', '40001', '40P01'}


@dataclass(frozen=True)
class ProductReadRequest:
    operation: str
    limit: int = 50
    offset: int = 0
    q: str | None = None
    category: str | None = None
    source: str | None = None
    brand: str | None = None
    product_id: str | None = None


@dataclass(frozen=True)
class ProductReadProposal:
    request: ProductReadRequest
    revision: int
    profile_signature: str
    activation: bool
    finalists: tuple[ProductEvidence, ...]
    evidence: tuple[ProductEvidence, ...]
    excluded_ids: tuple[str, ...]
    excluded_identity_signature: str


def _now():
    return datetime.now(timezone.utc)


def _owner(db, user_id):
    user = db.get(User, user_id)
    if user is None:
        raise RecommendationOwnerMissing()
    return user


def _activation(db, user_id, request):
    activation = db.get(ActivationSession, user_id)
    if request.operation == 'search' and (activation is None or not activation.completed_at):
        raise HTTPException(status_code=409, detail=CONFLICT_MESSAGE)
    return activation


def _anchor(db, request):
    anchor = db.get(Product, request.product_id)
    if anchor is None:
        raise HTTPException(status_code=404, detail='Товар не найден')
    return anchor


def _initialize(sessions, user_id, request):
    with sessions() as db:
        _owner(db, user_id)
        activation = _activation(db, user_id, request)
        if request.operation == 'complements':
            _anchor(db, request)
        # The legacy complement path has no Taste dependency. Do not introduce
        # profile writes for users whose existing path never needed a scorer.
        if activation is not None:
            ensure_style_profile(db, user_id)
            ensure_taste_profile(db, user_id)
            db.commit()


def _search_query(request, fit, *, as_of):
    query = select(Product).where(Product.is_active == 1, *fresh_stock_conditions(checked_at=as_of))
    gender = _fit_gender_condition(fit)
    if gender is not None:
        query = query.where(gender)
    if request.category:
        query = query.where(Product.category == request.category)
    if request.source and request.source.strip().lower() == 'demo':
        query = query.where(Product.source == 'demo')
    else:
        query = query.where(Product.source != 'demo')
        if request.source and request.source.strip():
            query = query.where(Product.source == request.source.strip())
    if request.brand and request.brand.strip():
        query = query.where(Product.brand.ilike(f'%{request.brand.strip()}%'))
    if request.q and request.q.strip():
        query = query.where(*search_conditions(request.q))
    return query.order_by(Product.created_at.desc(), Product.id)


def _prepare(sessions, user_id, request):
    with sessions() as db:
        if db.get_bind().dialect.name == 'postgresql':
            db.connection(execution_options={'isolation_level': 'REPEATABLE READ'})
            db.execute(text('SET TRANSACTION READ ONLY'))
        as_of = _now()
        db.info.update(recommendation_read_only=True, recommendation_as_of=as_of)
        begin_ranking(db)
        user = _owner(db, user_id)
        activation = _activation(db, user_id, request)
        signature = profile_input_signature(db, user_id)
        evidence, excluded_ids = {}, ()
        if request.operation == 'search':
            fit = db.scalar(select(FitProfile).where(FitProfile.user_id == user_id))
            rows = personalized_search(db, user, activation, _search_query(request, fit, as_of=as_of),
                limit=request.limit, offset=request.offset)
        else:
            anchor = _anchor(db, request)
            evidence[anchor.id] = ProductEvidence(anchor.id, anchor.source_id, product_evidence(anchor))
            rows = complementary_products(db, user, anchor, limit=request.limit)
            excluded_ids = tuple(db.info.get('prepared_product_complement_excluded_ids', (anchor.id,)))
        finalists = tuple(ProductEvidence(product.id, product.source_id, product_evidence(product)) for product in rows)
        evidence.update((row.product_id, row) for row in finalists)
        keys = set()
        if excluded_ids:
            with db.execute(identity_projection(excluded_ids)) as identities:
                for row in identities:
                    check_ranking_budget(db)
                    keys.update(product_identity_keys(row))
        check_ranking_budget(db)
        return ProductReadProposal(request, user.recommendation_revision, signature, activation is not None,
            finalists, tuple(evidence[key] for key in sorted(evidence)), tuple(sorted(excluded_ids)), identity_signature(keys))


def _publish(sessions, user_id, proposal):
    with sessions() as db:
        catalogue = lock_catalogue(db, proposal.evidence, excluded_ids=proposal.excluded_ids,
            excluded_identity_signature=proposal.excluded_identity_signature, max_finalists=201)
        with owner_mutation(db, user_id) as mutation:
            if (mutation.owner.recommendation_revision != proposal.revision
                    or profile_input_signature(db, user_id) != proposal.profile_signature):
                raise RecommendationSnapshotChanged()
            request = proposal.request
            activation = _activation(db, user_id, request)
            if (activation is not None) != proposal.activation:
                raise RecommendationSnapshotChanged()
            for expected in proposal.evidence:
                product = catalogue.products.get(expected.product_id)
                if product is None or catalogue.evidence[product.id] != expected.evidence:
                    raise RecommendationSnapshotChanged()
            fit = db.scalar(select(FitProfile).where(FitProfile.user_id == user_id))
            anchor = catalogue.products.get(request.product_id) if request.operation == 'complements' else None
            # An invalid anchor legitimately yields an empty result. Its full
            # fingerprint still prevents publication from a changed reference.
            if anchor is not None and proposal.finalists and not _hard_ok(anchor, fit=fit, rules_by_source=catalogue.rules):
                raise RecommendationSnapshotChanged()
            # Other exclusion identities were checked and locked before owner.
            # Unchanged finalist fingerprints preserve their earlier admission;
            # only this bounded reference is needed for runtime validation.
            keys = product_identity_keys(anchor) if anchor is not None else set()
            result = []
            for expected in proposal.finalists:
                product = catalogue.products[expected.product_id]
                if activation is not None:
                    valid = product_ok(product, fit, activation, rules=catalogue.rules)
                else:
                    valid = _hard_ok(product, fit=fit, rules_by_source=catalogue.rules)
                if request.operation == 'complements':
                    roles = COMPLEMENT_ROLES.get(product_role(anchor), ())
                    valid = (valid and product_role(product) in roles and pair_compatible(anchor, product)
                        and not (product_identity_keys(product) & keys))
                    if activation is not None:
                        valid = (valid and product_ok(anchor, fit, activation, rules=catalogue.rules)
                            and as_rubles(product.price) + as_rubles(anchor.price) <= as_rubles(fit.budget_max))
                    else:
                        valid = valid and _scenario_product_ok(product, 'gym' if is_sports_item(anchor) else 'daily')
                if not valid:
                    raise RecommendationSnapshotChanged()
                result.append(product_to_api(product))
        # No commit or accepted personal change: closing releases only locks.
        return result


def build_product_read_response(bind, user_id, *, operation, q=None, category=None, source=None,
        brand=None, limit=50, offset=0, product_id=None):
    if operation not in {'search', 'complements'}:
        raise ValueError('Unknown product read operation')
    request = ProductReadRequest(operation, min(200 if operation == 'search' else 12, max(1, int(limit))),
        min(5000, max(0, int(offset))), q, category, source, brand, product_id)
    sessions = sessionmaker(bind=bind, autoflush=False, expire_on_commit=False)
    try:
        _initialize(sessions, user_id, request)
        for attempt in range(2):
            preparing = True
            try:
                proposal = _prepare(sessions, user_id, request)
                preparing = False
                return _publish(sessions, user_id, proposal)
            except RecommendationSnapshotChanged:
                pass
            except DBAPIError as error:
                state = getattr(error.orig, 'sqlstate', None)
                if preparing and state == '57014':
                    raise RecommendationRankingTimeout() from error
                if state not in _RETRYABLE_SQLSTATES:
                    raise
            if attempt == 1:
                raise HTTPException(status_code=409, detail=CONFLICT_MESSAGE)
    except RecommendationRankingTimeout as error:
        raise HTTPException(status_code=409, detail=TIMEOUT_MESSAGE) from error
    except RecommendationOwnerMissing as error:
        raise HTTPException(status_code=401, detail='Войдите снова, чтобы продолжить.') from error
