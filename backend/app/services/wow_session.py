"""Complete activation once, with all outfit computation outside owner locks."""
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from ..models import ActivationSession, User
from . import activation_service as activation, outfit_engine_v2 as engine
from .outfit_session import OutfitChoice, freeze_choice, restore_choice
from .product_identity import product_identity_keys
from .profile_repository import ensure_style_profile, ensure_taste_profile, load_preferences
from .recommendation_budget import (RecommendationRankingTimeout, TIMEOUT_MESSAGE,
    begin_ranking, check_ranking_budget)
from .recommendation_publication import (ProductEvidence, identity_projection,
    identity_rows_signature, lock_catalogue, product_evidence)
from .recommendation_revision import (RecommendationOwnerMissing, RecommendationSnapshotChanged,
    owner_mutation, profile_input_signature)


@dataclass(frozen=True)
class WowBatch:
    scenario: str
    title: str
    choice: OutfitChoice
    previous_history_ids: tuple[str, ...]


@dataclass(frozen=True)
class WowProposal:
    revision: int
    profile_signature: str
    replay: bool
    batches: tuple[WowBatch, ...]
    evidence: tuple[ProductEvidence, ...]
    dependency_ids: tuple[str, ...]
    identity_signature: str


def _inputs(db, user_id):
    user = db.get(User, user_id)
    if user is None:
        raise RecommendationOwnerMissing()
    session = db.get(ActivationSession, user_id)
    if session is None:
        raise HTTPException(status_code=409, detail='Сначала укажите размеры, бюджет и стили.')
    profile, fit, taste = load_preferences(db, user)
    activation._require_activation_gender(fit)
    if activation.rated_count(session) < activation.MIN_SWIPES:
        raise HTTPException(status_code=409, detail='Оцените хотя бы 15 вещей, чтобы мы поняли ваш вкус.')
    if not activation.can_complete(session):
        raise HTTPException(status_code=409,
            detail='Пока ничего не понравилось. Найдите хотя бы одну подходящую вещь или измените предпочтения.')
    return user, session, profile, fit, taste


def _initialize(sessions, user_id):
    with sessions() as db:
        if db.get(User, user_id) is None:
            raise RecommendationOwnerMissing()
        if db.get(ActivationSession, user_id) is None:
            raise HTTPException(status_code=409, detail='Сначала укажите размеры, бюджет и стили.')
        ensure_style_profile(db, user_id)
        ensure_taste_profile(db, user_id)
        db.commit()


def _prepare(sessions, user_id):
    with sessions() as db:
        if db.get_bind().dialect.name == 'postgresql':
            db.connection(execution_options={'isolation_level': 'REPEATABLE READ'})
            db.execute(text('SET TRANSACTION READ ONLY'))
        db.info.update(recommendation_read_only=True, recommendation_as_of=datetime.now(timezone.utc))
        begin_ranking(db)
        user, session, profile, fit, taste = _inputs(db, user_id)
        signature = profile_input_signature(db, user_id)
        if session.completed_at:
            return WowProposal(user.recommendation_revision, signature, True, (), (), (), '')
        specs = [('daily', 'На каждый день'), ('evening', 'На выход'),
            ('daily', 'Похоже на ваши лайки')]
        batches, staged, used, products, dependencies = [], [], set(), {}, set()
        for scenario, title in specs:
            check_ranking_budget(db)
            batch = engine.prepare_outfit_batch(db, user, count=1, scenario=scenario, extra_existing=staged)
            dependencies.update(batch.dependency_ids)
            for choice in batch.selected:
                selected = [item.product for item in choice.items.values()]
                keys = set().union(*(product_identity_keys(p) for p in selected))
                if used & keys:
                    continue
                used.update(keys)
                products.update((p.id, p) for p in selected)
                if title == 'Похоже на ваши лайки' and not any(
                        any(float((taste.category_weights or {}).get(cat, 0)) > 0
                            for cat in activation.features(p)['category']) for p in selected):
                    title = 'Ваш стиль'
                batches.append(WowBatch(scenario, title, freeze_choice(choice), batch.previous_history_ids))
                staged.append(SimpleNamespace(style_direction=scenario,
                    items_json={slot: item.product.id for slot, item in choice.items.items()}))
        dependencies.update(products)
        with db.execute(identity_projection(dependencies)) as identities:
            identities_signature = identity_rows_signature(identities)
        check_ranking_budget(db)
        return WowProposal(user.recommendation_revision, signature, False, tuple(batches),
            tuple(ProductEvidence(p.id, p.source_id, product_evidence(p)) for p in products.values()),
            tuple(sorted(dependencies)), identities_signature)


def _publish(sessions, user_id, proposal):
    if proposal.replay:
        # Revalidate only issued rows. A lost response must not rank again,
        # consume quota or allocate a second set of outfits.
        with sessions() as db:
            db.info['recommendation_read_only'] = True
            user, session, profile, fit, _ = _inputs(db, user_id)
            if not session.completed_at:
                raise RecommendationSnapshotChanged()
            return activation.wow_response(db, user, session, profile, fit)
    with sessions() as db:
        catalogue = lock_catalogue(db, proposal.evidence, excluded_ids=proposal.dependency_ids,
            excluded_identity_rows_signature=proposal.identity_signature)
        with owner_mutation(db, user_id) as mutation:
            if (mutation.owner.recommendation_revision != proposal.revision
                    or profile_input_signature(db, user_id) != proposal.profile_signature):
                raise RecommendationSnapshotChanged()
            user, session, profile, fit, _ = _inputs(db, user_id)
            if session.completed_at:
                raise RecommendationSnapshotChanged()
            for expected in proposal.evidence:
                product = catalogue.products.get(expected.product_id)
                if (product is None or catalogue.evidence[product.id] != expected.evidence
                        or not activation.product_ok(product, fit, session, rules=catalogue.rules)):
                    raise RecommendationSnapshotChanged()
            snapshots, history = [], {}
            for batch in proposal.batches:
                selected = restore_choice(batch.choice, catalogue.products)
                if any(not engine._scenario_product_ok(item.product, batch.scenario)
                        for item in selected.items.values()):
                    raise RecommendationSnapshotChanged()
                history_id = str(uuid4())
                previous_history = tuple(history.get(batch.scenario, ())) + batch.previous_history_ids
                row = engine.persist_outfit_batch(db, user, engine.OutfitBatch(batch.scenario,
                    (selected,), f'outfit_rotation:{batch.scenario}',
                    previous_history, proposal.dependency_ids), replace_existing=False, history_id=history_id)[0]
                history.setdefault(batch.scenario, []).insert(0, history_id)
                snapshots.append({'id': row.id, 'title': batch.title,
                    'explanation': activation._wow_explanation(
                        [item.product for item in selected.items.values()], profile, fit)})
            session.wow_json = snapshots
            session.completed_at = datetime.now(timezone.utc)
            profile.completed_at = session.completed_at
            profile.onboarding_step = 4
            mutation.mark_changed()
            db.flush()
            response = activation.wow_response(db, user, session, profile, fit, rules=catalogue.rules)
            if len(response['outfits']) != len(snapshots):
                raise RecommendationSnapshotChanged()
        db.commit()
        return response


def build_wow_response(bind, user_id):
    sessions = sessionmaker(bind=bind, autoflush=False, expire_on_commit=False)
    try:
        _initialize(sessions, user_id)
        for attempt in range(2):
            preparing = True
            try:
                proposal = _prepare(sessions, user_id)
                preparing = False
                return _publish(sessions, user_id, proposal)
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
                    detail='Параметры или каталог изменились во время подбора. Повторите запрос — оценки сохранены.')
    except RecommendationRankingTimeout as error:
        raise HTTPException(status_code=409, detail=TIMEOUT_MESSAGE) from error
    except RecommendationOwnerMissing as error:
        raise HTTPException(status_code=401, detail='Войдите снова, чтобы продолжить.') from error
