"""Calculate an outfit batch in a read-only snapshot, then publish atomically."""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from ..models import ActivationSession, FitProfile, Outfit, User
from . import outfit_engine_v2 as engine
from .activation_policy import product_ok
from .profile_repository import ensure_style_profile, ensure_taste_profile
from .recommendation_budget import RecommendationRankingTimeout, TIMEOUT_MESSAGE, begin_ranking, check_ranking_budget
from .recommendation_cache_service import persist_recommendation_cache_ids
from .recommendation_publication import ProductEvidence, identity_projection, identity_rows_signature, lock_catalogue, product_evidence
from .recommendation_revision import RecommendationOwnerMissing, RecommendationSnapshotChanged, owner_mutation, profile_input_signature


@dataclass(frozen=True)
class OutfitItem:
    slot: str
    product_id: str
    personal_score: float
    fit_score: float
    taste_score: float
    context_score: float
    skip_penalty: float


@dataclass(frozen=True)
class OutfitChoice:
    items: tuple[OutfitItem, ...]
    total_price: Decimal
    personal_score: float
    compatibility_score: float
    novelty_score: float
    final_score: float
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class OutfitProposal:
    revision: int
    profile_signature: str
    scenario: str
    choices: tuple[OutfitChoice, ...]
    evidence: tuple[ProductEvidence, ...]
    previous_history_ids: tuple[str, ...]
    dependency_ids: tuple[str, ...]
    identity_signature: str
    candidate_ids: tuple[str, ...] | None
    top_ids: tuple[str, ...]


def freeze_choice(choice):
    return OutfitChoice(tuple(OutfitItem(slot, item.product.id, item.personal_score,
        item.fit_score, item.taste_score, item.context_score, item.skip_penalty)
        for slot, item in choice.items.items()), choice.total_price, choice.personal_score,
        choice.compatibility_score, choice.novelty_score, choice.final_score, tuple(choice.reasons))


def restore_choice(choice, products):
    items = {item.slot: engine.SlotCandidate(item.slot, products[item.product_id],
        item.personal_score, item.fit_score, item.taste_score, item.context_score, item.skip_penalty)
        for item in choice.items}
    return engine.OutfitCandidate(items, choice.total_price, choice.personal_score,
        choice.compatibility_score, choice.novelty_score, choice.final_score, list(choice.reasons))


def _owner(db, user_id):
    user = db.get(User, user_id)
    if user is None:
        raise RecommendationOwnerMissing()
    return user


def _initialize(sessions, user_id, *, only_if_empty=False):
    with sessions() as db:
        _owner(db, user_id)
        if only_if_empty and (db.get(ActivationSession, user_id) is not None
                or db.scalar(select(Outfit.id).where(Outfit.user_id == user_id).limit(1)) is not None):
            return False
        ensure_style_profile(db, user_id)
        ensure_taste_profile(db, user_id)
        db.commit()
        return True


def _prepare(sessions, user_id, *, count, scenario, only_if_empty=False):
    with sessions() as db:
        if db.get_bind().dialect.name == 'postgresql':
            db.connection(execution_options={'isolation_level': 'REPEATABLE READ'})
            db.execute(text('SET TRANSACTION READ ONLY'))
        db.info.update(recommendation_read_only=True, recommendation_as_of=datetime.now(timezone.utc))
        begin_ranking(db)
        user = _owner(db, user_id)
        signature = profile_input_signature(db, user_id)
        if only_if_empty and (db.get(ActivationSession, user_id) is not None
                or db.scalar(select(Outfit.id).where(Outfit.user_id == user_id).limit(1)) is not None):
            scenario_key = engine._scenario_key(scenario)
            batch = engine.OutfitBatch(scenario_key, (), f'outfit_rotation:{scenario_key}', (), ())
        else:
            batch = engine.prepare_outfit_batch(db, user, count=count, scenario=scenario)
        products = {item.product.id: item.product for choice in batch.selected for item in choice.items.values()}
        evidence = tuple(ProductEvidence(p.id, p.source_id, product_evidence(p)) for p in products.values())
        choices = tuple(freeze_choice(choice) for choice in batch.selected)
        identities_signature = identity_rows_signature(())
        if batch.dependency_ids:
            with db.execute(identity_projection(batch.dependency_ids)) as identities:
                identities_signature = identity_rows_signature(identities)
        check_ranking_budget(db)
        candidates = db.info.get('prepared_feed_candidate_ids')
        return OutfitProposal(user.recommendation_revision, signature, batch.scenario, choices, evidence,
            batch.previous_history_ids, batch.dependency_ids, identities_signature,
            tuple(candidates)[:400] if candidates is not None else None,
            tuple(db.info.get('prepared_feed_top_ids', ()))[:100])


def _publish(sessions, user_id, proposal, *, replace_existing, only_if_empty=False):
    from ..api.outfits import visible_outfits
    with sessions() as db:
        catalogue = lock_catalogue(db, proposal.evidence, excluded_ids=proposal.dependency_ids,
            excluded_identity_rows_signature=proposal.identity_signature)
        with owner_mutation(db, user_id) as mutation:
            if (mutation.owner.recommendation_revision != proposal.revision
                    or profile_input_signature(db, user_id) != proposal.profile_signature):
                raise RecommendationSnapshotChanged()
            fit = db.scalar(select(FitProfile).where(FitProfile.user_id == user_id))
            activation = db.get(ActivationSession, user_id)
            if only_if_empty and (activation is not None
                    or db.scalar(select(Outfit.id).where(Outfit.user_id == user_id).limit(1)) is not None):
                return []
            for expected in proposal.evidence:
                current = catalogue.products.get(expected.product_id)
                if (current is None or catalogue.evidence[current.id] != expected.evidence
                        or not engine._hard_ok(current, fit=fit, rules_by_source=catalogue.rules)
                        or not engine._scenario_product_ok(current, proposal.scenario)
                        or (activation is not None and not product_ok(current, fit, activation, rules=catalogue.rules))):
                    raise RecommendationSnapshotChanged()
            selected = [restore_choice(choice, catalogue.products) for choice in proposal.choices]
            batch = engine.OutfitBatch(proposal.scenario, tuple(selected), f'outfit_rotation:{proposal.scenario}',
                proposal.previous_history_ids, proposal.dependency_ids)
            rows = engine.persist_outfit_batch(db, mutation.owner, batch, replace_existing=replace_existing)
            if proposal.candidate_ids is not None:
                persist_recommendation_cache_ids(db, user_id=user_id,
                    candidate_ids=proposal.candidate_ids, top_ids=proposal.top_ids)
            response = visible_outfits(db, mutation.owner, rows, rules=catalogue.rules)
            if len(response) != len(rows):
                raise RecommendationSnapshotChanged()
        db.commit()
        return response


def build_outfits_response(bind, user_id, *, count=3, scenario='daily', replace_existing=True, only_if_empty=False):
    sessions = sessionmaker(bind=bind, autoflush=False, expire_on_commit=False)
    try:
        if not _initialize(sessions, user_id, only_if_empty=only_if_empty):
            return []
        for attempt in range(2):
            preparing = True
            try:
                proposal = _prepare(sessions, user_id, count=count, scenario=scenario, only_if_empty=only_if_empty)
                preparing = False
                return _publish(sessions, user_id, proposal, replace_existing=replace_existing, only_if_empty=only_if_empty)
            except RecommendationSnapshotChanged:
                pass
            except DBAPIError as error:
                state = getattr(error.orig, 'sqlstate', None)
                if preparing and state == '57014':
                    raise RecommendationRankingTimeout() from error
                if state not in {'55P03', '57014', '40001', '40P01'}:
                    raise
            if attempt == 1:
                raise HTTPException(status_code=409,
                    detail='Параметры или каталог изменились во время подбора. Обновите ленту.')
    except RecommendationRankingTimeout as error:
        raise HTTPException(status_code=409, detail=TIMEOUT_MESSAGE) from error
    except RecommendationOwnerMissing as error:
        raise HTTPException(status_code=401, detail='Войдите снова, чтобы продолжить.') from error
