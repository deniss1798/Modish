from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Product, TasteProfile, User, UserTasteFeature
from app.services.user_twin_service import (
  apply_decay,
  apply_event_to_user_twin,
  get_feature_preference,
  get_user_taste_features,
)


def _product(
  *,
  category: str = "рубашки",
  brand: str = "brand-a",
  color: str = "black",
  style: str = "minimalism",
) -> Product:
  return Product(
    id=str(uuid4()),
    external_id=str(uuid4()),
    source="test-shop",
    title=f"{color} {category}",
    brand=brand,
    category=category,
    price=4990,
    image_url="https://example.test/image.jpg",
    product_url="https://example.test/product",
    affiliate_url="https://example.test/affiliate",
    available_sizes=["M"],
    colors=[color],
    gender_target="womenswear",
    style_tags=[style],
    is_available=1,
    is_active=1,
    is_deleted_from_feed=0,
  )


class UserTwinServiceTests(unittest.TestCase):
  def setUp(self) -> None:
    self.engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(self.engine)
    self.Session = sessionmaker(bind=self.engine, autoflush=False, future=True)
    self.db = self.Session()
    now = datetime.now(timezone.utc)
    self.user = User(id=str(uuid4()), email="twin@test.io", password_hash="x")
    self.db.add(self.user)
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
    self.product = _product()
    self.db.add(self.product)
    self.db.flush()

  def tearDown(self) -> None:
    self.db.close()

  def test_repeated_positive_signals_increase_confidence(self) -> None:
    apply_event_to_user_twin(
      self.db,
      user_id=self.user.id,
      product=self.product,
      event_type="like",
    )
    first = get_feature_preference(
      self.db,
      user_id=self.user.id,
      feature_type="category",
      feature_value="рубашки",
    )
    self.assertIsNotNone(first)
    first_confidence = float(first.confidence)
    first_score = float(first.preference_score)

    for _ in range(5):
      apply_event_to_user_twin(
        self.db,
        user_id=self.user.id,
        product=self.product,
        event_type="like",
      )
    current = get_feature_preference(
      self.db,
      user_id=self.user.id,
      feature_type="category",
      feature_value="рубашки",
    )
    self.assertIsNotNone(current)
    self.assertGreater(current.preference_score, first_score)
    self.assertGreater(current.confidence, first_confidence)
    self.assertGreater(current.confidence, 0.6)

  def test_repeated_negative_signals_decrease_preference(self) -> None:
    for _ in range(3):
      apply_event_to_user_twin(
        self.db,
        user_id=self.user.id,
        product=self.product,
        event_type="like",
      )
    before = get_feature_preference(
      self.db,
      user_id=self.user.id,
      feature_type="color",
      feature_value="black",
    )
    self.assertIsNotNone(before)
    self.assertGreater(before.preference_score, 0.0)
    before_score = float(before.preference_score)

    for _ in range(2):
      apply_event_to_user_twin(
        self.db,
        user_id=self.user.id,
        product=self.product,
        event_type="dislike",
        meta={"reason": "dont_like_color"},
      )
    after = get_feature_preference(
      self.db,
      user_id=self.user.id,
      feature_type="color",
      feature_value="black",
    )
    self.assertIsNotNone(after)
    self.assertLess(after.preference_score, before_score)
    self.assertLess(after.preference_score, 0.0)
    self.assertEqual(after.negative_count, 2)

  def test_purchase_dominates_like(self) -> None:
    like_product = _product(category="платья", brand="brand-like", color="white")
    purchase_product = _product(category="брюки", brand="brand-purchase", color="navy")
    self.db.add_all([like_product, purchase_product])
    self.db.flush()

    apply_event_to_user_twin(
      self.db,
      user_id=self.user.id,
      product=like_product,
      event_type="like",
    )
    apply_event_to_user_twin(
      self.db,
      user_id=self.user.id,
      product=purchase_product,
      event_type="purchase",
    )

    liked = get_feature_preference(
      self.db,
      user_id=self.user.id,
      feature_type="category",
      feature_value="платья",
    )
    purchased = get_feature_preference(
      self.db,
      user_id=self.user.id,
      feature_type="category",
      feature_value="брюки",
    )
    self.assertIsNotNone(liked)
    self.assertIsNotNone(purchased)
    self.assertGreater(purchased.preference_score, liked.preference_score)

  def test_old_weak_events_decay(self) -> None:
    old = datetime.now(timezone.utc) - timedelta(days=90)
    feature = UserTasteFeature(
      id=str(uuid4()),
      user_id=self.user.id,
      feature_type="style",
      feature_value="minimalism",
      preference_score=0.6,
      confidence=0.5,
      positive_count=1,
      negative_count=0,
      created_at=old,
      updated_at=old,
    )
    self.db.add(feature)
    self.db.flush()

    apply_decay(feature, as_of=datetime.now(timezone.utc))

    self.assertLess(feature.preference_score, 0.3)
    self.assertLess(feature.confidence, 0.5)

  def test_get_user_taste_features_orders_by_confidence(self) -> None:
    for _ in range(4):
      apply_event_to_user_twin(
        self.db,
        user_id=self.user.id,
        product=self.product,
        event_type="save",
      )
    rows = get_user_taste_features(
      self.db,
      user_id=self.user.id,
      feature_type="category",
      min_confidence=0.2,
    )
    self.assertTrue(rows)
    self.assertEqual(rows[0].feature_value, "рубашки")


if __name__ == "__main__":
  unittest.main()
