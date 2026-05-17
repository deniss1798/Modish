from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from ..services.product_analytics_service import analytics_summary
from .deps import get_db

router = APIRouter(tags=["analytics"])


def _admin_token_ok(authorization: str | None) -> bool:
  expected = (os.getenv("ADMIN_CATALOG_TOKEN") or "").strip()
  if not expected:
    return False
  if not authorization or not authorization.lower().startswith("bearer "):
    return False
  return authorization.split(" ", 1)[1].strip() == expected


@router.get("/analytics/summary")
def get_analytics_summary(
  days: int = 7,
  db: Session = Depends(get_db),
  authorization: str | None = Header(default=None),
) -> dict[str, Any]:
  if not _admin_token_ok(authorization):
    raise HTTPException(status_code=403, detail="Admin token required")
  return analytics_summary(db, days=days)
