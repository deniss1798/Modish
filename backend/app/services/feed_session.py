"""Read-only feed ranking followed by a bounded, current-evidence publication.

No ORM objects cross phases. PostgreSQL ranking uses a repeatable-read, read-only
transaction. Source/policy/product locks precede the short owner lock so import
contention cannot hold up this owner's feedback queue. One conflict may rerank;
a second conflict returns an actionable 409 without partial delivery writes.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from ..models import ActivationSession, FitProfile, RecommendationEventV2, User, UserProductState
from .activation_policy import product_ok
from .activation_service import require_warm_feed
from .catalog.rule_filters import product_passes_source_rules
from .feed_delivery import serialize_feed_delivery
from .product_eligibility import evaluate_eligibility
from .profile_repository import ensure_style_profile, ensure_taste_profile
from .product_identity import product_identity_keys
from .recommendation_cache_service import persist_recommendation_cache_ids
from .recommendation_engine import ScoredProduct, expand_product_exclusions, generate_feed
from .recommendation_publication import (
    product_evidence as _product_evidence,
    identity_projection as _identity_projection,
    identity_signature as _identity_signature,
    lock_catalogue,
)
from .recommendation_revision import RecommendationOwnerMissing, RecommendationSnapshotChanged, owner_mutation, profile_input_signature


@dataclass(frozen=True)
class FeedFinalist:
    product_id: str
    source_id: str | None
    evidence: str
    final_score: float
    breakdown: tuple
    reason: str
    reasons: tuple
    candidate_sources: tuple
    algorithm_version: str
    ranking_algorithm: str


@dataclass(frozen=True)
class FeedProposal:
    revision: int
    profile_signature: str
    activation: bool
    finalists: tuple[FeedFinalist, ...]
    candidate_ids: tuple[str, ...]
    excluded_ids: tuple[str, ...]
    excluded_identity_signature: str


def _exclusions(db, user_id, requested, as_of):
    hidden = set(requested or ())
    hidden.update(db.scalars(select(UserProductState.product_id).where(
        UserProductState.user_id == user_id, UserProductState.hidden_until > as_of)))
    hidden.update(db.scalars(select(RecommendationEventV2.product_id).where(
        RecommendationEventV2.user_id == user_id, RecommendationEventV2.product_id.is_not(None),
        RecommendationEventV2.event_type.in_(['like', 'dislike', 'skip', 'save']),
        RecommendationEventV2.created_at >= as_of - timedelta(days=30)).distinct()))
    return set(map(str, hidden))


def _initialize(sessions, user_id):
    with sessions() as db:
        user = db.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=401, detail='Войдите снова, чтобы продолжить.')
        # History admission can be expensive for a legacy account: do this
        # outside an owner lock. Preparation rechecks it in its own snapshot.
        require_warm_feed(db, user)
        ensure_style_profile(db, user_id)
        ensure_taste_profile(db, user_id)
        db.commit()


def _prepare(sessions, user_id, *, limit, source, scenario, filters, exclude_ids):
    with sessions() as db:
        if db.get_bind().dialect.name == 'postgresql':
            db.connection(execution_options={'isolation_level': 'REPEATABLE READ'})
            db.execute(text('SET TRANSACTION READ ONLY'))
        as_of = datetime.now(timezone.utc)
        db.info.update(recommendation_read_only=True, recommendation_as_of=as_of)
        user = db.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=401, detail='Войдите снова, чтобы продолжить.')
        require_warm_feed(db, user)
        signature = profile_input_signature(db, user_id)
        revision = user.recommendation_revision
        activation_session = db.get(ActivationSession, user_id)
        activation = activation_session is not None
        excluded_ids = _exclusions(db, user_id, exclude_ids, as_of)
        if activation:
            excluded_ids.update(pid for pid, reaction in (activation_session.swipes_json or {}).items()
                if reaction in {'dislike', 'unavailable'})
        else:
            excluded_ids = expand_product_exclusions(db, excluded_ids)
        excluded_keys = set()
        if excluded_ids:
            with db.execute(_identity_projection(excluded_ids)) as identities:
                for row in identities:
                    excluded_keys.update(product_identity_keys(row))
        scored = generate_feed(db, user, limit=limit, source=source, scenario=scenario,
            filters=filters, exclude_product_ids=excluded_ids, read_only=True)
        finalists = tuple(FeedFinalist(row.product.id, row.product.source_id, _product_evidence(row.product),
            row.final_score, tuple(row.breakdown.items()), row.reason, tuple(row.reasons),
            tuple(row.candidate_sources), row.algorithm_version, row.ranking_algorithm)
            for row in scored[:100])
        # Closing the read transaction releases its connection/cursors. Only
        # scalar/tuple DTOs leave this scope. Finalists/cache are bounded;
        # exclusion IDs remain complete, including for an empty result.
        return FeedProposal(revision, signature, activation, finalists,
            tuple(db.info.get('prepared_feed_candidate_ids', ()))[:400],
            tuple(sorted(excluded_ids)), _identity_signature(excluded_keys))


def _publish(sessions, user_id, proposal, *, source, scenario, filters):
    with sessions() as db:
        catalogue = lock_catalogue(db, proposal.finalists, excluded_ids=proposal.excluded_ids,
            excluded_identity_signature=proposal.excluded_identity_signature)
        with owner_mutation(db, user_id) as mutation:
            if mutation.owner.recommendation_revision != proposal.revision:
                raise RecommendationSnapshotChanged()
            if profile_input_signature(db, user_id) != proposal.profile_signature:
                raise RecommendationSnapshotChanged()
            fit = db.scalar(select(FitProfile).where(FitProfile.user_id == user_id))
            activation = db.get(ActivationSession, user_id)
            if (activation is not None) != proposal.activation:
                raise RecommendationSnapshotChanged()
            accepted = []
            for row in proposal.finalists:
                product = catalogue.products.get(row.product_id)
                if product is None or catalogue.evidence[row.product_id] != row.evidence:
                    raise RecommendationSnapshotChanged()
                valid = (product_ok(product, fit, activation, rules=catalogue.rules) if activation else
                    evaluate_eligibility(product, fit, filters=filters).eligible
                    and product_passes_source_rules(product, catalogue.rules))
                if not valid or (filters and not filters.matches(product)):
                    raise RecommendationSnapshotChanged()
                accepted.append(ScoredProduct(product, row.final_score, dict(row.breakdown), row.reason,
                    list(row.reasons), list(row.candidate_sources), row.algorithm_version, row.ranking_algorithm))
            if activation:
                activation.issued_feed_ids = list(dict.fromkeys(
                    [*(activation.issued_feed_ids or []), *(row.product_id for row in proposal.finalists)]))[-200:]
            else:
                persist_recommendation_cache_ids(db, user_id=user_id, candidate_ids=proposal.candidate_ids,
                    top_ids=[row.product_id for row in proposal.finalists])
            response = serialize_feed_delivery(db, user_id=user_id, scored=accepted, source=source, scenario=scenario)
        db.commit()
        return response


def build_feed_response(bind, user_id, *, limit=30, source=None, scenario='daily', filters=None, exclude_ids=()):
    sessions = sessionmaker(bind=bind, autoflush=False, expire_on_commit=False)
    try:
        _initialize(sessions, user_id)
    except RecommendationOwnerMissing as error:
        raise HTTPException(status_code=401, detail='Войдите снова, чтобы продолжить.') from error
    for attempt in range(2):
        try:
            proposal = _prepare(sessions, user_id, limit=limit, source=source, scenario=scenario,
                filters=filters, exclude_ids=tuple(exclude_ids or ()))
            return _publish(sessions, user_id, proposal, source=source, scenario=scenario, filters=filters)
        except RecommendationSnapshotChanged:
            pass
        except RecommendationOwnerMissing as error:
            raise HTTPException(status_code=401, detail='Войдите снова, чтобы продолжить.') from error
        except DBAPIError as error:
            if getattr(error.orig, 'sqlstate', None) not in {'55P03', '57014', '40001', '40P01'}:
                raise
        if attempt == 1:
            raise HTTPException(status_code=409,
                detail='Параметры или каталог изменились во время подбора. Обновите ленту.')
