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


@router.post("/outfits/generate")
def outfits_generate(
  count: int = 3,
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> list[dict[str, Any]]:
  user = user_from_token(credentials, db)
  items = generate_outfits(db, user, count=count)
  db.commit()
  out: list[dict[str, Any]] = []
  for o in items:
    products: dict[str, Any] = {}
    for slot, pid in (o.items_json or {}).items():
      p = db.execute(select(Product).where(Product.id == str(pid))).scalar_one_or_none()
      if p is not None:
        products[str(slot)] = product_to_api(p)
    out.append(outfit_to_api(o, products))
  return out


@router.get("/outfits")
def outfits_list(
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> list[dict[str, Any]]:
  user = user_from_token(credentials, db)
  rows = db.execute(
    select(Outfit).where(Outfit.user_id == user.id).order_by(Outfit.created_at.desc()).limit(50)
  ).scalars().all()
  out: list[dict[str, Any]] = []
  for o in rows:
    products: dict[str, Any] = {}
    for slot, pid in (o.items_json or {}).items():
      p = db.execute(select(Product).where(Product.id == str(pid))).scalar_one_or_none()
      if p is not None:
        products[str(slot)] = product_to_api(p)
    out.append(outfit_to_api(o, products))
  return out


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
