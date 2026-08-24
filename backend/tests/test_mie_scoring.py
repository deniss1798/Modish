from __future__ import annotations

import unittest
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import FitProfile, Product, TasteProfile, User, UserTasteFeature
from app.services.recommendation_config import FIT_MIN_THRESHOLD
from app.services.recommendation_engine import build_feed_context, score_product
from app.services.user_twin_service import apply_event_to_user_twin


def _product(
  *,
  category: str = "рубашки",
  brand: str = "brand-a",
  color: str = "black",
  style: str = "minimalism",
  gender: str = "womenswear",
  price: int = 4990,
) -> Product:
  return Product(
    id=str(uuid4()),
    external_id=str(uuid4()),
    source="test-shop",
    title=f"{color} {category}",
    brand=brand,
    category=category,
    price=price,
    image_url="https://example.test/image.jpg",
    product_url="https://example.test/product",
    affiliate_url="https://example.test/affiliate",
    available_sizes=["M"],
    colors=[color],
    gender_target=gender,
    style_tags=[style],
    is_available=1,
    is_active=1,
    is_deleted_from_feed=0,
  )


class MIEScoringTests(unittest.TestCase):
  def setUp(self) -> None:
    self.engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(self.engine)
    self.Session = sessionmaker(bind=self.engine, autoflush=False, future=True)
    self.db = self.Session()
    now = datetime.now(timezone.utc)
    self.user = User(id=str(uuid4()), email="mie@test.io", password_hash="x")
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
        style_scenarios=["daily", "minimalism"],
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
    self.db.flush()

  def tearDown(self) -> None:
    self.db.close()

  def test_score_breakdown_contains_normalized_components(self) -> None:
    product = _product()
    self.db.add(product)
    self.db.flush()

    scored = score_product(self.db, self.user, product)

    for key in (
      "fit_score",
      "taste_score",
      "context_score",
      "quality_score",
      "exploration_score",
      "final_score",
    ):
      self.assertIn(key, scored.breakdown)
      self.assertGreaterEqual(scored.breakdown[key], 0.0)
      self.assertLessEqual(scored.breakdown[key], 1.0)
    self.assertEqual(scored.breakdown["hard_reject"], 0.0)
    self.assertEqual(scored.final_score, scored.breakdown["final_score"])

  def test_fit_hard_filter_cannot_be_compensated_by_taste(self) -> None:
    product = _product(gender="menswear", category="рубашки", brand="dream-brand")
    self.db.add(product)
    self.db.flush()
    now = datetime.now(timezone.utc)
    for feature_type, feature_value in (
      ("item", product.id),
      ("category", "рубашки"),
      ("brand", "dream-brand"),
      ("style", "minimalism"),
      ("color", "black"),
      ("price_band", "mid"),
    ):
      self.db.add(
        UserTasteFeature(
          id=str(uuid4()),
          user_id=self.user.id,
          feature_type=feature_type,
          feature_value=feature_value,
          preference_score=1.0,
          confidence=1.0,
          positive_count=20,
          negative_count=0,
          last_positive_at=now,
          last_signal_at=now,
          last_decay_at=now,
          created_at=now,
          updated_at=now,
        )
      )
    self.db.flush()

    scored = score_product(self.db, self.user, product)

    self.assertLess(scored.breakdown["fit_score"], FIT_MIN_THRESHOLD)
    self.assertGreater(scored.breakdown["taste_score"], 0.8)
    self.assertEqual(scored.breakdown["hard_reject"], 1.0)
    self.assertEqual(scored.final_score, 0.0)

  def test_taste_ranking_changes_after_repeated_feedback(self) -> None:
    neutral = _product(category="рубашки", brand="neutral", color="white", style="classic")
    preferred = _product(category="платья", brand="preferred", color="black", style="minimalism")
    self.db.add_all([neutral, preferred])
    self.db.flush()

    ctx_before = build_feed_context(self.db, self.user, product_ids=[neutral.id, preferred.id])
    neutral_before = score_product(self.db, self.user, neutral, ctx_before)
    preferred_before = score_product(self.db, self.user, preferred, ctx_before)
    self.assertAlmostEqual(
      neutral_before.breakdown["taste_score"],
      preferred_before.breakdown["taste_score"],
      delta=0.05,
    )

    for _ in range(8):
      apply_event_to_user_twin(
        self.db,
        user_id=self.user.id,
        product=preferred,
        event_type="save",
      )
    ctx_after = build_feed_context(self.db, self.user, product_ids=[neutral.id, preferred.id])
    neutral_after = score_product(self.db, self.user, neutral, ctx_after)
    preferred_after = score_product(self.db, self.user, preferred, ctx_after)

    self.assertGreater(preferred_after.breakdown["taste_score"], neutral_after.breakdown["taste_score"])
    self.assertGreater(preferred_after.final_score, preferred_before.final_score)


if __name__ == "__main__":
  unittest.main()
