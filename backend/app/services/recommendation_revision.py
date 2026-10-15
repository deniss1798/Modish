"""Explicit owner mutations, before splitting ranking from publication.

The caller owns commit/rollback. Nested helpers share the same owner lock and
mark accepted semantic changes explicitly. Delivery/cache allocation is not a
taste mutation. Coalescing is scoped to the actual SessionTransaction, including
savepoints; rolling one back must not suppress a later outer revision update.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from functools import wraps
from hashlib import sha256
import inspect as python_inspect
import json

from sqlalchemy import event, inspect, select, update
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

from ..models import ActivationSession, FitProfile, StyleProfile, TasteProfile, User

_KEY = '_recommendation_owner_mutations'


@dataclass
class OwnerMutation:
    db: Session
    owner: User
    changed: bool = False
    bumped: bool = False
    depth: int = 0
    profile_depth: int = 0

    def mark_changed(self):
        """Call only after accepting a change; retries/no-ops do not mark."""
        self.changed = True

    def _publish_revision(self):
        if self.changed and not self.bumped:
            revision = self.db.scalar(update(User).where(User.id == self.owner.id).values(
                recommendation_revision=User.recommendation_revision + 1,
            ).returning(User.recommendation_revision).execution_options(synchronize_session=False))
            set_committed_value(self.owner, 'recommendation_revision', revision)
            self.bumped = True


@event.listens_for(Session, 'after_transaction_end')
def _forget_transaction(db, transaction):
    states = db.info.get(_KEY)
    if states is not None:
        states.pop(transaction, None)
        if not states:
            db.info.pop(_KEY, None)


@contextmanager
def owner_mutation(db: Session, user_id: str):
    """Lock owner first, reload previously cached personal rows once, then edit.

    Use before taking an Intent row lock. Already-owned transaction helpers do
    not expire outer pending changes. Existing caller edits are flushed only
    after acquiring the owner lock, before expiring cached personal rows.
    """
    transaction = db.get_nested_transaction() or db.get_transaction()
    if transaction is None:
        transaction = db.begin()
    owners = db.info.setdefault(_KEY, {}).setdefault(transaction, {})
    mutation = owners.get(user_id)
    if mutation is None:
        with db.no_autoflush:
            db.execute(select(User.id).where(User.id == user_id).with_for_update()).scalar_one()
        db.flush()
        # Expire only loaded rows for this owner. Do not aggregate an unbounded
        # personal history while holding the lock or discard pending edits.
        for row in list(db.identity_map.values()):
            state = inspect(row)
            if state.dict.get('user_id') == user_id and row.__class__.__module__ == User.__module__:
                db.expire(row)
        owner = db.scalar(select(User).where(User.id == user_id).execution_options(populate_existing=True))
        mutation = owners[user_id] = OwnerMutation(db, owner)
    before = mutation.changed
    mutation.depth += 1
    try:
        yield mutation
    except BaseException:
        mutation.changed = before
        raise
    else:
        if mutation.depth == 1:
            mutation._publish_revision()
    finally:
        mutation.depth -= 1


def _semantic_value(value):
    if isinstance(value, dict):
        return {key: _semantic_value(item) for key, item in value.items() if key != 'confirmed_at'}
    if isinstance(value, (list, tuple)):
        return [_semantic_value(item) for item in value]
    return value


def profile_input_signature(db: Session, user_id: str) -> str:
    """Bounded small profile graph; history/features use explicit event marks.

    Flush while the caller owns the owner lock so new rows and SQL expressions
    are included. Audit clocks and delivery allocations are not ranking inputs.
    Never print this projection or use User credentials/email in the signature.
    """
    db.flush()
    owner = db.get(User, user_id)
    values = {'owner': {key: getattr(owner, key) for key in ('height_cm', 'weight_kg', 'fit_preference')}}
    common = {'id', 'user_id', 'created_at', 'updated_at'}
    for model, ignored in (
            (FitProfile, set()), (StyleProfile, set()),
            (TasteProfile, {'profile_version'}),
            (ActivationSession, {'version', 'swipe_product_ids', 'issued_feed_ids', 'wow_json'})):
        row = db.scalar(select(model).where(model.user_id == user_id))
        values[model.__tablename__] = None if row is None else {
            column.name: _semantic_value(getattr(row, column.name))
            for column in model.__table__.columns if column.name not in common | ignored
        }
    encoded = json.dumps(values, sort_keys=True, ensure_ascii=True, default=str, separators=(',', ':'))
    return sha256(encoded.encode('utf-8')).hexdigest()


@contextmanager
def profile_input_mutation(db: Session, user_id: str):
    """Track semantic profile edits; the caller commits after leaving this scope."""
    with owner_mutation(db, user_id) as mutation:
        track = mutation.profile_depth == 0
        mutation.profile_depth += 1
        try:
            if track:
                before = profile_input_signature(db, user_id)
            yield mutation
            if track and before != profile_input_signature(db, user_id):
                mutation.mark_changed()
        finally:
            mutation.profile_depth -= 1


def profile_input_writer(owner_argument: str):
    """Explicit enrollment for commit-free profile service helpers only."""
    def decorate(function):
        signature = python_inspect.signature(function)

        @wraps(function)
        def wrapped(*args, **kwargs):
            arguments = signature.bind(*args, **kwargs).arguments
            owner = arguments[owner_argument]
            user_id = owner if owner_argument == 'user_id' else (
                owner.id if isinstance(owner, User) else owner.user_id)
            with profile_input_mutation(arguments['db'], user_id):
                return function(*args, **kwargs)
        return wrapped
    return decorate


def accept_personal_change(db: Session, user_id: str):
    """Enroll an accepted history/feature change before the caller commits.

    Existing event writers already own the owner lock before editing. This
    explicit mark also covers evidence which leaves all small profile fields
    unchanged. Call only for an actual accepted mutation, not an idempotent read.
    """
    with owner_mutation(db, user_id) as mutation:
        mutation.mark_changed()
