from __future__ import annotations

import hashlib
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AffiliateClick, Product, ProductSource
from ..services.catalog.affiliate_link_service import resolve_outbound_url
from .deps import auth_scheme, get_db, user_from_token

router = APIRouter(tags=["affiliate"])


@router.post("/affiliate/click/{product_id}")
def affiliate_click(
  product_id: str,
  request: Request,
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
  user_agent: str | None = Header(default=None, alias="User-Agent"),
) -> dict[str, Any]:
  user = user_from_token(credentials, db)
  p = db.execute(select(Product).where(Product.id == product_id)).scalar_one_or_none()
  if p is None or not p.is_active:
    raise HTTPException(
      status_code=status.HTTP_404_NOT_FOUND,
      detail="Товар временно недоступен",
    )
  src: ProductSource | None = None
  if p.source_id:
    src = db.execute(select(ProductSource).where(ProductSource.id == p.source_id)).scalar_one_or_none()
  url = resolve_outbound_url(p, src)
  if not url or not str(url).strip():
    raise HTTPException(
      status_code=status.HTTP_400_BAD_REQUEST,
      detail="Товар временно недоступен",
    )

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
