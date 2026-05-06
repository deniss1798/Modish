"""Каталог: список товаров и рекомендованная выдача."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Product, User, UserProductState
from ..schemas.api_product import product_to_api
from ..services.recommendation_engine import generate_feed
from .deps import auth_scheme, get_db, user_from_token

router = APIRouter(tags=["products"])


def _feed_scored_items(
  db: Session,
  user: User,
  *,
  limit: int,
) -> list[dict[str, Any]]:
  from datetime import datetime, timezone

  now = datetime.now(timezone.utc)
  hidden_ids = set(
    db.execute(
      select(UserProductState.product_id).where(
        UserProductState.user_id == user.id,
        UserProductState.hidden_until.is_not(None),
        UserProductState.hidden_until > now,
      )
    ).scalars()
  )
  scored = generate_feed(db, user, limit=limit, exclude_product_ids=set(map(str, hidden_ids)))
  out: list[dict[str, Any]] = []
  for s in scored:
    out.append(
      {
        "product": product_to_api(s.product),
        "final_score": s.final_score,
        "breakdown": s.breakdown,
        "reason": s.reason,
        "reasons": s.reasons,
      }
    )
  return out


@router.get("/products")
def products_list(
  limit: int = 50,
  offset: int = 0,
  category: str | None = None,
  db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
  q = select(Product).where(Product.is_active == 1)
  if category:
    q = q.where(Product.category == category)
  q = q.order_by(Product.created_at.desc()).offset(max(0, offset)).limit(min(200, max(1, limit)))
  rows = db.execute(q).scalars().all()
  return [product_to_api(p) for p in rows]


@router.get("/products/{product_id}")
def products_get(product_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
  from fastapi import HTTPException

  p = db.execute(select(Product).where(Product.id == product_id)).scalar_one_or_none()
  if p is None:
    raise HTTPException(status_code=404, detail="Product not found")
  return product_to_api(p)


@router.get("/products/recommended")
def products_recommended(
  limit: int = 30,
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> list[dict[str, Any]]:
  u = user_from_token(credentials, db)
  return _feed_scored_items(db, u, limit=limit)


@router.get("/feed")
def feed(
  limit: int = 30,
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> list[dict[str, Any]]:
  u = user_from_token(credentials, db)
  return _feed_scored_items(db, u, limit=limit)
