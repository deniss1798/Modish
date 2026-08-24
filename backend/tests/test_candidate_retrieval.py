from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import FitProfile, Product, TasteProfile, User, UserTasteFeature
from app.services.recommendation_engine import generate_feed


def _product(*, category: str, brand: str, updated_at: datetime) -> Product:
  return Product(
    id=str(uuid4()),
    external_id=str(uuid4()),
    source="test-shop",
    title=f"{brand} {category}",
    brand=brand,
    category=category,
    price=3990,
    image_url="https://example.test/image.jpg",
    product_url="https://example.test/product",
    affiliate_url="https://example.test/affiliate",
    available_sizes=["M"],
    colors=["black"],
    gender_target="womenswear",
    style_tags=["minimalism"],
    is_available=1,
    is_active=1,
    is_deleted_from_feed=0,
    created_at=updated_at,
    updated_at=updated_at,
  )


class CandidateRetrievalTests(unittest.TestCase):
  def setUp(self) -> None:
    self.engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(self.engine)
    self.Session = sessionmaker(bind=self.engine, autoflush=False, future=True)
    self.db = self.Session()
    now = datetime.now(timezone.utc)
    self.user = User(id=str(uuid4()), email="retrieval@test.io", password_hash="x")
    self.db.add(self.user)
    self.db.add(
      FitProfile(
        id=str(uuid4()),
        user_id=self.user.id,
        height_cm=170,
        gender_target="womenswear",
        clothing_size="M",
        budget_min=0,
        budget_max=10_000,
        interest_categories=[],
        style_scenarios=["daily"],
      )
    )
    self.db.add(
      TasteProfile(
        id=str(uuid4()),
        user_id=self.user.id,
        liked_categories=[],
        disliked_categories=[],
        liked_colors=[],
        disliked_colors=[],
        liked_brands=[],
        disliked_brands=[],
        liked_styles=[],
        disliked_styles=[],
        category_weights={},
        brand_weights={},
        color_weights={},
        style_weights={},
        price_min=0,
        price_max=10_000,
        preferred_fit="regular",
        profile_version=1,
        created_at=now,
        updated_at=now,
      )
    )
    old = now - timedelta(days=900)
    self.preferred = _product(category="платья", brand="old-perfect", updated_at=old)
    self.db.add(self.preferred)
    recent_products = [
      _product(category="рубашки", brand=f"recent-{i}", updated_at=now - timedelta(seconds=i))
      for i in range(1105)
    ]
    self.db.add_all(recent_products)
    self.db.add(
      UserTasteFeature(
        id=str(uuid4()),
        user_id=self.user.id,
        feature_type="category",
        feature_value="платья",
        preference_score=1.0,
        confidence=1.0,
        positive_count=12,
        negative_count=0,
        last_positive_at=now,
        last_signal_at=now,
        last_decay_at=now,
        created_at=now,
        updated_at=now,
      )
    )
    self.db.flush()

  def tearDown(self) -> None:
    self.db.close()

  def test_candidate_retrieval_searches_beyond_latest_products(self) -> None:
    feed = generate_feed(self.db, self.user, limit=5, max_to_score=80)

    ids = [item.product.id for item in feed]
    self.assertIn(self.preferred.id, ids)
    preferred = next(item for item in feed if item.product.id == self.preferred.id)
    self.assertIn("taste", preferred.candidate_sources)
