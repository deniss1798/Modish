from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Product, RecommendationCandidatePool, UserRecommendationCache

if TYPE_CHECKING:
  from .recommendation_engine import ScoredProduct


def persist_recommendation_caches(
  db: Session,
  *,
  user_id: str,
  filtered_products: list[Product],
  scored_top: list[ScoredProduct],
  candidate_limit: int = 400,
  top_limit: int = 100,
) -> None:
  """Сохраняет пул кандидатов и топ выдачи для пользователя (ТЗ §13)."""
  now = datetime.now(timezone.utc)
  cands = [p.id for p in filtered_products[: max(1, candidate_limit)]]
  top_ids = [s.product.id for s in scored_top[: max(1, top_limit)]]

  pool = db.execute(
    select(RecommendationCandidatePool).where(RecommendationCandidatePool.user_id == user_id)
  ).scalar_one_or_none()
  if pool is None:
    pool = RecommendationCandidatePool(
      id=str(uuid4()),
      user_id=user_id,
      product_ids=cands,
      created_at=now,
      updated_at=now,
    )
    db.add(pool)
  else:
    pool.product_ids = cands
    pool.updated_at = now

  cache = db.execute(
    select(UserRecommendationCache).where(UserRecommendationCache.user_id == user_id)
  ).scalar_one_or_none()
  if cache is None:
    cache = UserRecommendationCache(
      id=str(uuid4()),
      user_id=user_id,
      top_product_ids=top_ids,
      created_at=now,
      updated_at=now,
    )
    db.add(cache)
  else:
    cache.top_product_ids = top_ids
    cache.updated_at = now

  db.commit()
