from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from .deps import auth_scheme, get_db, user_from_token
from ..schemas.shopping_intent import IntentCreateRequest, IntentRefineRequest, IntentOutfitReplace, IntentOutfitAction, IntentFeedbackRequest, IntentCloseRequest
from ..services.activation_service import require_warm_feed
from ..services import shopping_intent_service as service

router = APIRouter(prefix='/shopping-intents', tags=['shopping-intents'])


def _execute(db, user, operation, *, remember=False):
    require_warm_feed(db, user)
    try:
        result = service.intent_response(db, user, operation(), remember=remember)
        db.commit()
        return result
    except service.IntentError as error:
        db.rollback()
        raise HTTPException(status_code=error.status, detail=str(error)) from error


@router.post('')
def create(payload: IntentCreateRequest, db: Session = Depends(get_db),
           credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme)):
    user = user_from_token(credentials, db)
    return _execute(db, user, lambda: service.create_intent(db, user, **payload.model_dump()), remember=True)


@router.get('/{intent_id}')
def read(intent_id: str, db: Session = Depends(get_db),
         credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme)):
    user = user_from_token(credentials, db)
    return _execute(db, user, lambda: service.get_intent(db, user, intent_id))


@router.post('/{intent_id}/refine')
def refine(intent_id: str, payload: IntentRefineRequest, db: Session = Depends(get_db),
           credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme)):
    user = user_from_token(credentials, db)
    return _execute(db, user, lambda: service.refine_intent(db, user, intent_id, **payload.model_dump()), remember=True)


@router.post('/{intent_id}/outfits/{outfit_id}/replace')
def replace_outfit(intent_id: str, outfit_id: str, payload: IntentOutfitReplace,
                   db: Session = Depends(get_db), credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme)):
    user = user_from_token(credentials, db)
    return _execute(db, user, lambda: service.replace_outfit_item(db, user, intent_id, outfit_id, **payload.model_dump()), remember=True)


@router.post('/{intent_id}/outfits/{outfit_id}/save')
def save_outfit(intent_id: str, outfit_id: str, payload: IntentOutfitAction,
                db: Session = Depends(get_db), credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme)):
    user = user_from_token(credentials, db)
    require_warm_feed(db, user)
    try:
        result = service.save_intent_outfit(db, user, intent_id, outfit_id, **payload.model_dump())
        db.commit()
        return result
    except service.IntentError as error:
        db.rollback()
        raise HTTPException(status_code=error.status, detail=str(error)) from error


@router.post('/{intent_id}/feedback')
def feedback(intent_id: str, payload: IntentFeedbackRequest,
             db: Session = Depends(get_db), credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme)):
    user = user_from_token(credentials, db)
    return _execute(db, user, lambda: service.feedback(db, user, intent_id, **payload.model_dump()), remember=True)


@router.post('/{intent_id}/close')
def close(intent_id: str, payload: IntentCloseRequest, db: Session = Depends(get_db),
          credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme)):
    user=user_from_token(credentials,db)
    try:
        result=service.close_intent(db,user,intent_id,**payload.model_dump())
        db.commit()
        return result
    except service.IntentError as error:
        db.rollback()
        raise HTTPException(status_code=error.status,detail=str(error)) from error
