from __future__ import annotations

import hashlib
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models import AffiliateClick, Product, ProductSource, User
from ..services.catalog.affiliate_link_service import resolve_outbound_url

router = APIRouter(tags=["affiliate"])
auth_scheme = HTTPBearer(auto_error=False)


def get_db() -> Any:
  db = SessionLocal()
  try:
    yield db
  finally:
    db.close()


def _user_from_token(
  credentials: HTTPAuthorizationCredentials | None,
  db: Session,
) -> User:
  from ..main import _user_from_token as _jwt_user  # noqa: PLC0415

  return _jwt_user(credentials, db)


@router.post("/affiliate/click/{product_id}")
def affiliate_click(
  product_id: str,
  request: Request,
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
  user_agent: str | None = Header(default=None, alias="User-Agent"),
) -> dict[str, Any]:
  user = _user_from_token(credentials, db)
  p = db.execute(select(Product).where(Product.id == product_id)).scalar_one_or_none()
  if p is None or not p.is_active:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
  src: ProductSource | None = None
  if p.source_id:
    src = db.execute(select(ProductSource).where(ProductSource.id == p.source_id)).scalar_one_or_none()
  url = resolve_outbound_url(p, src)
  if not url:
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No outbound URL for product")

  ip = request.client.host if request.client else ""
  ip_hash = hashlib.sha256(f"{ip}".encode("utf-8")).hexdigest()[:32] if ip else None
  click = AffiliateClick(
    id=str(uuid4()),
    user_id=user.id,
    product_id=p.id,
    source_id=p.source_id,
    affiliate_url=url,
    click_id=str(uuid4())[:32],
    user_agent=user_agent,
    ip_hash=ip_hash,
  )
  db.add(click)
  db.commit()
  return {"url": url, "click_id": click.click_id}
