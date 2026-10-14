"""Authenticated browse/shop entry; no cold-start or fit-completion shortcut."""
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from .deps import auth_scheme, get_db, user_from_token
from ..services.catalog_discovery import DiscoveryError, catalogue_detail, catalogue_page

router = APIRouter(prefix='/catalog/discovery', tags=['catalogue discovery'])


@router.get('')
def list_catalogue(category: str | None = Query(default=None, max_length=40),
                   q: str | None = Query(default=None, max_length=160),
                   source: str | None = Query(default=None, max_length=64),
                   min_price: int | None = Query(default=None, ge=0),
                   max_price: int | None = Query(default=None, ge=0),
                   cursor: str | None = Query(default=None, max_length=1024),
                   limit: int = Query(default=20, ge=1, le=40),
                   db: Session = Depends(get_db),
                   credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme)):
  user = user_from_token(credentials, db)
  try:
    return catalogue_page(db, user, category=category, q=q, source=source,
        min_price=min_price, max_price=max_price, cursor=cursor, limit=limit)
  except DiscoveryError as error:
    raise HTTPException(status_code=error.status, detail=str(error)) from error


@router.get('/{product_id}')
def read_catalogue_product(product_id: str, db: Session = Depends(get_db),
                           credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme)):
  user = user_from_token(credentials, db)
  try:
    return catalogue_detail(db, user, product_id)
  except DiscoveryError as error:
    raise HTTPException(status_code=error.status, detail=str(error)) from error
