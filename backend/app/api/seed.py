from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Recommendation


def seed_recommendations(db: Session, user_id: str) -> None:
  existing = db.execute(
    select(Recommendation).where(Recommendation.user_id == user_id)
  ).scalars().first()
  if existing:
    return
  base = Recommendation(
    id=str(uuid4()),
    user_id=user_id,
    type="outfit",
    title="Образ на каждый день",
    description="Спокойный минималистичный образ для повседневного использования.",
    content_json={
      "items": {
        "top": "белый лонгслив",
        "bottom": "прямые тёмные джинсы",
        "layer": "серый жакет",
        "shoes": "белые кроссовки",
      },
      "why_it_fits": ["чистый силуэт", "легко повторить", "спокойный контраст"],
      "alternatives": [
        "лонгслив можно заменить на белую футболку",
        "кроссовки можно заменить на лоферы",
      ],
    },
    tags_json={
      "styles": ["minimal", "smart_casual"],
      "colors": ["white", "navy", "gray"],
      "silhouettes": ["straight", "structured"],
      "occasion": ["daily"],
      "item_types": ["longsleeve", "jeans", "jacket", "sneakers"],
    },
    status="active",
  )
  db.add(base)
  db.commit()
