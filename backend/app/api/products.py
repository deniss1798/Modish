"""Каталог: список товаров и рекомендованная выдача."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Product, User, UserProductState, FitProfile
from ..schemas.api_product import product_to_api
from ..services.recommendation_config import recommendation_algorithm_metadata
from ..services.recommendation_engine import generate_feed
from ..services.catalog_filters import CatalogFilters, CATEGORY_GROUPS
from .deps import auth_scheme, get_db, user_from_token

router = APIRouter(tags=["products"])


def _feed_scored_items(
  db: Session,
  user: User,
  *,
  limit: int,
  source: str | None = None,
  scenario: str = "daily",
  filters: CatalogFilters | None = None,
  exclude_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
  from datetime import datetime, timezone

  from fastapi import HTTPException

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
  hidden_ids.update(exclude_ids or [])
  try:
    scored = generate_feed(
      db,
      user,
      limit=limit,
      exclude_product_ids=set(map(str, hidden_ids)),
      source=source,
      scenario=scenario,
      filters=filters,
    )
  except Exception as exc:
    db.rollback()
    raise HTTPException(
      status_code=500,
      detail="Не удалось загрузить ленту. Попробуйте обновить её через несколько секунд.",
    ) from exc

  from ..services.product_analytics_service import log_feed_impressions

  ranking_metadata: dict[str, dict[str, Any]] = {}
  for rank, s in enumerate(scored, start=1):
    ranking_metadata[s.product.id] = {
      **recommendation_algorithm_metadata(ranking_algorithm=s.ranking_algorithm, scenario=scenario),
      "rank": rank,
      "candidate_source": ",".join(s.candidate_sources),
      "candidate_sources": list(s.candidate_sources),
      "fit_score": s.breakdown.get("fit_score"),
      "taste_score": s.breakdown.get("taste_score"),
      "context_score": s.breakdown.get("context_score"),
      "quality_score": s.breakdown.get("quality_score"),
      "exploration_score": s.breakdown.get("exploration_score"),
      "final_score": s.final_score,
    }
  log_feed_impressions(
    db,
    user_id=user.id,
    product_ids=[s.product.id for s in scored],
    source=source,
    ranking_metadata=ranking_metadata,
  )
  out: list[dict[str, Any]] = []
  for s in scored:
    out.append(
      {
        "product": product_to_api(s.product),
        "final_score": s.final_score,
        "breakdown": s.breakdown,
        "candidate_sources": s.candidate_sources,
        "algorithm_version": s.algorithm_version,
        "ranking_algorithm": s.ranking_algorithm,
        "reason": s.reason,
        "reasons": s.reasons,
      }
    )
  db.commit()
  return out


@router.get("/products")
def products_list(
  limit: int = 50,
  offset: int = 0,
  category: str | None = None,
  source: str | None = None,
  brand: str | None = None,
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> list[dict[str, Any]]:
  from ..services.candidate_retrieval_service import _fit_gender_condition
  from ..services.catalog.rule_filters import product_gender_compatible
  from ..services.catalog.catalog_quality import product_is_feed_eligible
  fit = None
  if credentials:
    user = user_from_token(credentials, db)
    fit = db.execute(select(FitProfile).where(FitProfile.user_id == user.id)).scalar_one_or_none()
  q = select(Product).where(Product.is_active == 1)
  gender_condition = _fit_gender_condition(fit)
  if gender_condition is not None:
    q = q.where(gender_condition)
  if category:
    q = q.where(Product.category == category)
  if source and source.strip().lower() == "demo":
    q = q.where(Product.source == "demo")
  else:
    q = q.where(Product.source != "demo")
    if source and source.strip():
      q = q.where(Product.source == source.strip())
  if brand and brand.strip():
    q = q.where(Product.brand.ilike(f"%{brand.strip()}%"))
  q = q.order_by(Product.created_at.desc(), Product.id)
  rows = []
  skipped = 0
  for p in db.execute(q.execution_options(yield_per=200)).scalars():
    if not product_is_feed_eligible(p) or not product_gender_compatible(p, fit.gender_target if fit else None):
      continue
    if skipped < max(0, offset):
      skipped += 1
      continue
    rows.append(product_to_api(p))
    if len(rows) >= min(200, max(1, limit)):
      break
  return rows


@router.get("/products/brands")
def products_brands(
  limit: int = 80,
  db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
  """Список брендов с количеством товаров (быстрый GROUP BY)."""
  cap = min(200, max(1, limit))
  rows = db.execute(
    select(Product.brand, func.count(Product.id))
    .where(
      Product.is_active == 1,
      Product.is_deleted_from_feed == 0,
      Product.source != "demo",
      Product.brand.is_not(None),
      Product.brand != "",
    )
    .group_by(Product.brand)
    .order_by(func.count(Product.id).desc())
    .limit(cap)
  ).all()
  return [{"brand": str(b).strip(), "count": int(c)} for b, c in rows if str(b).strip()]


@router.get("/products/recommended")
def products_recommended(
  limit: int = 30,
  source: str | None = None,
  scenario: str = "daily",
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> list[dict[str, Any]]:
  u = user_from_token(credentials, db)
  return _feed_scored_items(db, u, limit=limit, source=source, scenario=scenario)


@router.get("/feed")
def feed(
  limit: int = 30,
  source: str | None = None,
  scenario: str = "daily",
  min_price: int | None = Query(default=None, ge=0),
  max_price: int | None = Query(default=None, ge=0),
  categories: list[str] = Query(default=[]),
  sizes: list[str] = Query(default=[]),
  colors: list[str] = Query(default=[]),
  exclude_ids: list[str] = Query(default=[], max_length=120),
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> list[dict[str, Any]]:
  u = user_from_token(credentials, db)
  if min_price is not None and max_price is not None and min_price > max_price:
    raise HTTPException(status_code=422, detail="Минимальная цена больше максимальной")
  if any(c not in CATEGORY_GROUPS for c in categories):
    raise HTTPException(status_code=422, detail="Неизвестная категория")
  filters = CatalogFilters(min_price, max_price, tuple(categories), tuple(sizes), tuple(colors))
  return _feed_scored_items(db, u, limit=limit, source=source, scenario=scenario, filters=filters, exclude_ids=exclude_ids)


@router.get("/products/{product_id}")
def products_get(product_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
  p = db.execute(select(Product).where(Product.id == product_id)).scalar_one_or_none()
  if p is None:
    raise HTTPException(status_code=404, detail="Товар не найден")
  return product_to_api(p)


@router.get("/products/{product_id}/complements")
def product_complements(
  product_id: str,
  limit: int = Query(default=6, ge=1, le=12),
  db: Session = Depends(get_db),
  credentials: HTTPAuthorizationCredentials | None = Depends(auth_scheme),
) -> list[dict[str, Any]]:
  from ..services.product_complements import complementary_products
  user = user_from_token(credentials, db)
  anchor = db.get(Product, product_id)
  if anchor is None:
    raise HTTPException(status_code=404, detail="Товар не найден")
  return [product_to_api(p) for p in complementary_products(db, user, anchor, limit=limit)]
