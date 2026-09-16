from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import FitProfile, Outfit, Product, User
from ..services.outfit_engine_v2 import SCENARIO_SLOT_OPTIONS, _hard_ok, _scenario_key, product_slot
from ..services.outfit_quality import scenario_product_ok, outfit_compatible
from ..services.product_identity import product_identity_keys
from ..services.catalog.rule_filters import load_rules_by_source_id
from ..services.outfit_service import generate_outfits
from .deps import auth_scheme, get_db, user_from_token
from .serializers import outfit_to_api
from ..schemas.api_product import product_to_api

router = APIRouter(tags=["outfits"])


def _outfit_products(db: Session, o: Outfit) -> dict[str, Any]:
  products: dict[str, Any] = {}
  for slot, pid in (o.items_json or {}).items():
    if slot not in {"one_piece", "top", "bottom", "shoes"}:
      continue
    p = db.execute(select(Product).where(Product.id == str(pid))).scalar_one_or_none()
    if p is not None:
      products[str(slot)] = product_to_api(p)
  return products


def visible_outfits(db: Session, user: User, rows: list[Outfit]) -> list[dict[str, Any]]:
  """Validate older stored outfits too, without deleting saved user data."""
  fit = db.execute(select(FitProfile).where(FitProfile.user_id == user.id)).scalar_one_or_none()
  rules = load_rules_by_source_id(db)
  ids = {str(pid) for row in rows for pid in (row.items_json or {}).values() if pid}
  products = {p.id: p for p in db.execute(select(Product).where(Product.id.in_(ids))).scalars()} if ids else {}
  result = []
  previous_keys: list[tuple[str, set[str]]] = []
  for row in rows:
    scenario = _scenario_key(row.style_direction)
    slots = {s: products.get(str(pid)) for s, pid in (row.items_json or {}).items() if s in {"one_piece", "top", "bottom", "shoes"}}
    if not any(set(slots) == set(option) for option in SCENARIO_SLOT_OPTIONS[scenario]):
      continue
    if any(p is None or product_slot(p) != s or not scenario_product_ok(p, scenario) or not _hard_ok(p, fit=fit, rules_by_source=rules) for s, p in slots.items()):
      continue
    if not outfit_compatible(list(slots.values())):
      continue
    keys = set().union(*(product_identity_keys(p) for p in slots.values()))
    if any(sum(bool(product_identity_keys(p) & old) for p in slots.values()) > len(slots) - (2 if old_scenario == scenario else 1) for old_scenario, old in previous_keys):
      continue
    previous_keys.append((scenario, keys))
    item = outfit_to_api(row, {s: product_to_api(p) for s, p in slots.items()})
    item["items"] = {s: p.id for s, p in slots.items()}
    item["total_price"] = sum(int(p.price or 0) for p in slots.values())
    result.append(item)
  return result


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
      detail="Не удалось собрать образы. Попробуйте ещё раз через несколько секунд.",
    ) from exc
  return visible_outfits(db, user, items)


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
  return visible_outfits(db, user, rows)


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
