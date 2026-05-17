from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import AffiliateClick, Product, ProductImpression, RecommendationEventV2


def log_feed_impressions(
  db: Session,
  *,
  user_id: str,
  product_ids: list[str],
  source: str | None = None,
) -> None:
  """Не ломает выдачу ленты, если миграция impressions ещё не применена."""
  if not product_ids:
    return
  try:
    now = datetime.now(timezone.utc)
    src = (source or "").strip()[:64] or None
    for pid in product_ids[:100]:
      if not pid:
        continue
      db.add(
        ProductImpression(
          id=str(uuid4()),
          user_id=user_id,
          product_id=str(pid),
          source=src,
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
