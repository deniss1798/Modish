from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import AffiliateClick, Product, ProductImpression, RecommendationEventV2
from .recommendation_config import ALGORITHM_VERSION


def log_feed_impressions(
  db: Session,
  *,
  user_id: str,
  product_ids: list[str],
  source: str | None = None,
  ranking_metadata: dict[str, dict[str, Any]] | None = None,
) -> None:
  """Не ломает выдачу ленты, если миграция impressions ещё не применена."""
  if not product_ids:
    return
  try:
    now = datetime.now(timezone.utc)
    src = (source or "").strip()[:64] or None
    ranking_metadata = ranking_metadata or {}
    for pid in product_ids[:100]:
      if not pid:
        continue
      meta = dict(ranking_metadata.get(str(pid), {}))
      algorithm_version = str(meta.get("algorithm_version") or ALGORITHM_VERSION).strip()[:64] or None
      candidate_source = meta.get("candidate_source")
      if candidate_source is None and isinstance(meta.get("candidate_sources"), list):
        candidate_source = ",".join(str(x) for x in meta["candidate_sources"] if str(x).strip())
      db.add(
        ProductImpression(
          id=str(uuid4()),
          user_id=user_id,
          product_id=str(pid),
          source=src,
          algorithm_version=algorithm_version,
          candidate_source=str(candidate_source or "").strip() or None,
          final_score=float(meta["final_score"]) if meta.get("final_score") is not None else None,
          rank_position=int(meta["rank"]) if meta.get("rank") is not None else None,
          ranking_meta_json=meta,
          created_at=now,
        )
      )
  except Exception:
    # Аналитика не должна откатывать ленту, если таблица ещё не создана.
    pass


def analytics_summary(db: Session, *, days: int = 7) -> dict[str, Any]:
  since = datetime.now(timezone.utc) - timedelta(days=max(1, min(90, days)))

  impressions = int(
    db.execute(
      select(func.count())
      .select_from(ProductImpression)
      .where(ProductImpression.created_at >= since)
    ).scalar_one()
    or 0
  )
  clicks = int(
    db.execute(
      select(func.count()).select_from(AffiliateClick).where(AffiliateClick.created_at >= since)
    ).scalar_one()
    or 0
  )
  saves = int(
    db.execute(
      select(func.count())
      .select_from(RecommendationEventV2)
      .where(
        RecommendationEventV2.created_at >= since,
        RecommendationEventV2.event_type == "save",
      )
    ).scalar_one()
    or 0
  )

  ctr = (clicks / impressions * 100.0) if impressions > 0 else 0.0

  top_saved = db.execute(
    select(Product.id, Product.title, Product.brand, func.count().label("cnt"))
    .join(RecommendationEventV2, RecommendationEventV2.product_id == Product.id)
    .where(
      RecommendationEventV2.created_at >= since,
      RecommendationEventV2.event_type == "save",
    )
    .group_by(Product.id, Product.title, Product.brand)
    .order_by(func.count().desc())
    .limit(10)
  ).all()

  top_clicked = db.execute(
    select(Product.id, Product.title, Product.brand, func.count().label("cnt"))
    .join(AffiliateClick, AffiliateClick.product_id == Product.id)
    .where(AffiliateClick.created_at >= since)
    .group_by(Product.id, Product.title, Product.brand)
    .order_by(func.count().desc())
    .limit(10)
  ).all()

  return {
    "period_days": days,
    "impressions": impressions,
    "clicks": clicks,
    "saves": saves,
    "ctr_percent": round(ctr, 2),
    "top_saved": [
      {"product_id": r[0], "title": r[1], "brand": r[2], "count": int(r[3])} for r in top_saved
    ],
    "top_clicked": [
      {"product_id": r[0], "title": r[1], "brand": r[2], "count": int(r[3])} for r in top_clicked
    ],
  }
