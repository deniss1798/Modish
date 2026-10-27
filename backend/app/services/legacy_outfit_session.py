"""One short atomic publication for a real legacy outfit batch and its quota."""
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import User
from .outfit_policy import requested_outfit_count


def _quota(db, user):
    # Lazy imports avoid business -> recommendation_service -> session cycles.
    from .. import business
    try:
        return business.assert_can_generate(db, user)
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error


def _project_and_charge(db, user, rows, response, rules):
    if not response:
        return []
    from ..api.serializers import rec_to_dict
    from .recommendation_service import persist_legacy_recommendations
    limits = _quota(db, user)
    projected = persist_legacy_recommendations(db, user.id, response)
    limits.recommendation_batches_used += 1
    limits.updated_at = datetime.now(timezone.utc)
    db.flush()
    return [rec_to_dict(row) for row in projected]


def build_legacy_outfits_response(bind, user_id, *, count=3, scenario='daily'):
    from .outfit_session import build_outfits_response
    # Admission gives fast quota feedback, but is never the authority for debit.
    # A simultaneous request must still pass _quota under the publication lock.
    with Session(bind, autoflush=False) as db:
        user = db.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=401, detail='Войдите снова, чтобы продолжить.')
        _quota(db, user)
        db.commit()
    return build_outfits_response(bind, user_id, count=requested_outfit_count(count),
        scenario=scenario, replace_existing=False, publish_transform=_project_and_charge,
        allow_clothing_base=False)
