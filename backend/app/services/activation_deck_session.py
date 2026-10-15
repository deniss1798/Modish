"""Style Swipe catalogue scans do not own the account's feedback lock."""
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from ..models import ActivationSession, FitProfile, Product, User
from ..schemas.api_product import product_to_api
from .activation_service import MIN_SWIPES, can_complete, product_ok, rated_count, swipe_deck
from .product_identity import product_identity_keys
from .profile_repository import ensure_style_profile, ensure_taste_profile
from .recommendation_publication import ProductEvidence, identity_projection, identity_signature, lock_catalogue, product_evidence
from .recommendation_revision import RecommendationOwnerMissing, RecommendationSnapshotChanged, owner_mutation, profile_input_signature
from .recommendation_budget import begin_ranking, check_ranking_budget, RecommendationRankingTimeout, TIMEOUT_MESSAGE


@dataclass(frozen=True)
class DeckProposal:
    revision: int
    profile_signature: str
    allocation_signature: str
    finalists: tuple[ProductEvidence, ...]
    allocation: tuple[str, ...]
    excluded_ids: tuple[str, ...]
    excluded_identity_signature: str
    target: int


def _allocation_signature(ids):
    return sha256(json.dumps(list(ids or ()), ensure_ascii=True,
        separators=(',', ':')).encode('utf-8')).hexdigest()


def _owner(db, user_id):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail='Войдите снова, чтобы продолжить.')
    return user


def _activation(db, user_id):
    session = db.get(ActivationSession, user_id)
    if session is None:
        raise HTTPException(status_code=409, detail='Сначала укажите размеры, бюджет и стили.')
    return session


def _prepare(sessions, user_id):
    with sessions() as db:
        if db.get_bind().dialect.name == 'postgresql':
            db.connection(execution_options={'isolation_level': 'REPEATABLE READ'})
            db.execute(text('SET TRANSACTION READ ONLY'))
        db.info.update(recommendation_read_only=True, recommendation_as_of=datetime.now(timezone.utc))
        begin_ranking(db)
        user, activation = _owner(db, user_id), _activation(db, user_id)
        excluded_ids = tuple(sorted(set(activation.swipe_product_ids or ()) | set(activation.swipes_json or {})))
        keys = set()
        if excluded_ids:
            with db.execute(identity_projection(excluded_ids)) as identities:
                for row in identities:
                    keys.update(product_identity_keys(row))
        allocation_signature = _allocation_signature(activation.swipe_product_ids)
        signature = profile_input_signature(db, user_id)
        response = swipe_deck(db, user, activation, read_only=True)
        ids = [row['id'] for row in response['products'][:30]]
        products = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_(ids)))} if ids else {}
        finalists = tuple(ProductEvidence(pid, products[pid].source_id, product_evidence(products[pid])) for pid in ids)
        check_ranking_budget(db)
        return DeckProposal(user.recommendation_revision, signature, allocation_signature, finalists,
            db.info['prepared_swipe_product_ids'], excluded_ids, identity_signature(keys), response['target'])


def _publish(sessions, user_id, proposal):
    with sessions() as db:
        catalogue = lock_catalogue(db, proposal.finalists, excluded_ids=proposal.excluded_ids,
            excluded_identity_signature=proposal.excluded_identity_signature)
        with owner_mutation(db, user_id) as mutation:
            activation = _activation(db, user_id)
            if (mutation.owner.recommendation_revision != proposal.revision
                    or profile_input_signature(db, user_id) != proposal.profile_signature
                    or _allocation_signature(activation.swipe_product_ids) != proposal.allocation_signature):
                raise RecommendationSnapshotChanged()
            fit = db.scalar(select(FitProfile).where(FitProfile.user_id == user_id))
            accepted = []
            for row in proposal.finalists:
                product = catalogue.products.get(row.product_id)
                if (product is None or catalogue.evidence[row.product_id] != row.evidence
                        or not product_ok(product, fit, activation, rules=catalogue.rules)):
                    raise RecommendationSnapshotChanged()
                accepted.append(product_to_api(product))
            activation.swipe_product_ids = list(proposal.allocation)
            response = {'products': accepted, 'rated': rated_count(activation), 'target': proposal.target,
                'minimum': MIN_SWIPES, 'can_continue': can_complete(activation)}
        db.commit()
        return response


def build_swipe_deck(bind, user_id):
    sessions = sessionmaker(bind=bind, autoflush=False, expire_on_commit=False)
    try:
        with sessions() as db:
            _owner(db, user_id)
            _activation(db, user_id)
            ensure_style_profile(db, user_id)
            ensure_taste_profile(db, user_id)
            db.commit()
        for attempt in range(2):
            preparing = True
            try:
                proposal = _prepare(sessions, user_id)
                preparing = False
                return _publish(sessions, user_id, proposal)
            except RecommendationSnapshotChanged:
                pass
            except RecommendationRankingTimeout as error:
                raise HTTPException(status_code=409, detail=TIMEOUT_MESSAGE) from error
            except DBAPIError as error:
                if preparing and getattr(error.orig, 'sqlstate', None) == '57014':
                    raise HTTPException(status_code=409, detail=TIMEOUT_MESSAGE) from error
                if getattr(error.orig, 'sqlstate', None) not in {'55P03', '57014', '40001', '40P01'}:
                    raise
            if attempt == 1:
                raise HTTPException(status_code=409,
                    detail='Карточка больше не подходит вашим параметрам. Обновите подборку.')
    except RecommendationOwnerMissing as error:
        raise HTTPException(status_code=401, detail='Войдите снова, чтобы продолжить.') from error
