from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Outfit, Product, User
from ..services.outfit_service import generate_outfits
from .deps import auth_scheme, get_db, user_from_token
from .serializers import outfit_to_api
from ..schemas.api_product import product_to_api

router = APIRouter(tags=["outfits"])


def _outfit_products(db: Session, o: Outfit) -> dict[str, Any]:
  products: dict[str, Any] = {}
  for slot, pid in (o.items_json or {}).items():
    p = db.execute(select(Product).where(Product.id == str(pid))).scalar_one_or_none()
    if p is not None:
      products[str(slot)] = product_to_api(p)
  return products


@router.post("/outfits/generate")
def outfits_generate(
  count: int = 3,
  scenario: str = "daily",
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> list[dict[str, Any]]:
  user = user_from_token(credentials, db)
  try:
    items = generate_outfits(db, user, count=count, scenario=scenario)
    db.commit()
  except Exception as exc:
    db.rollback()
    raise HTTPException(
      status_code=500,
      detail=f"Не удалось собрать образы: {exc!s}",
    ) from exc
  return [outfit_to_api(o, _outfit_products(db, o)) for o in items]


@router.get("/outfits")
def outfits_list(
  scenario: str | None = None,
  saved_only: bool = False,
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> list[dict[str, Any]]:
  user = user_from_token(credentials, db)
  q = select(Outfit).where(Outfit.user_id == user.id)
  if saved_only:
    q = q.where(Outfit.is_saved == 1)
  if scenario and scenario.strip():
    q = q.where(Outfit.style_direction == scenario.strip().lower())
  rows = db.execute(q.order_by(Outfit.created_at.desc()).limit(50)).scalars().all()
  return [outfit_to_api(o, _outfit_products(db, o)) for o in rows]


@router.post("/outfits/save")
def outfits_save(
  outfit_id: str,
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, Any]:
  user = user_from_token(credentials, db)
  o = db.execute(
    select(Outfit).where(Outfit.id == outfit_id, Outfit.user_id == user.id)
  ).scalar_one_or_none()
  if o is None:
    raise HTTPException(status_code=404, detail="Outfit not found")
  o.is_saved = 1
  o.updated_at = datetime.now(timezone.utc)
  db.commit()
  return {"status": "ok", "outfit_id": outfit_id}


@router.post("/outfits/unsave")
def outfits_unsave(
  outfit_id: str,
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> dict[str, Any]:
  user = user_from_token(credentials, db)
  o = db.execute(
    select(Outfit).where(Outfit.id == outfit_id, Outfit.user_id == user.id)
  ).scalar_one_or_none()
  if o is None:
    raise HTTPException(status_code=404, detail="Outfit not found")
  o.is_saved = 0
  o.updated_at = datetime.now(timezone.utc)
  db.commit()
  return {"status": "ok", "outfit_id": outfit_id}
