from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import FitProfile, MetricEvent, Product, RecommendationEventV2, TasteProfile, User
from app.services.outfit_engine_v2 import (
  SlotCandidate,
  compatibility_score,
  generate_outfits_v2,
)
from app.services.outfit_service import generate_outfits
from app.services.recommendation_config import OUTFIT_ENGINE_VERSION


def _product(
  *,
  title: str,
  category: str,
  color: str = "black",
  style: str = "minimalism",
  price: int = 3990,
) -> Product:
  now = datetime.now(timezone.utc)
  return Product(
    id=str(uuid4()),
    external_id=str(uuid4()),
    source="test-shop",
    title=title,
    brand="test-brand",
    category=category,
    price=price,
    image_url="https://example.test/image.jpg",
    product_url="https://example.test/product",
    affiliate_url="https://example.test/affiliate",
    available_sizes=["M"],
    colors=[color],
    color_family=color,
    material="cotton",
    occasion="daily",
    gender_target="womenswear",
    fit="regular",
    silhouette="straight",
    style_tags=[style],
    is_available=1,
    is_active=1,
    is_deleted_from_feed=0,
    created_at=now,
    updated_at=now,
  )


class OutfitEngineV2Tests(unittest.TestCase):
  def setUp(self) -> None:
    self.engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(self.engine)
    self.Session = sessionmaker(bind=self.engine, autoflush=False, future=True)
    self.db = self.Session()
    now = datetime.now(timezone.utc)
    self.user = User(id=str(uuid4()), email="outfit-v2@test.io", password_hash="x")
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
    self.products = [
      _product(title="Black minimal shirt", category="рубашки"),
      _product(title="White minimal shirt", category="рубашки", color="white"),
      _product(title="Black straight trousers", category="брюки"),
      _product(title="Gray straight trousers", category="брюки", color="gray"),
      _product(title="Black leather shoes", category="обувь"),
      _product(title="White leather shoes", category="обувь", color="white"),
      _product(title="Black compact bag", category="сумки"),
    ]
    self.db.add_all(self.products)
    self.db.flush()

  def tearDown(self) -> None:
    self.db.close()
    self.engine.dispose()

  def test_generate_outfits_v2_creates_scored_slot_combination(self) -> None:
    outfits = generate_outfits_v2(self.db, self.user, count=2, scenario="daily")

    self.assertGreaterEqual(len(outfits), 1)
    first = outfits[0]
    self.assertIn("top", first.items_json)
    self.assertIn("bottom", first.items_json)
    self.assertIn("shoes", first.items_json)
    self.assertGreater(first.score, 0.0)
    self.assertIn(OUTFIT_ENGINE_VERSION, first.reason)

  def test_generate_outfits_wrapper_uses_v2_when_possible(self) -> None:
    outfits = generate_outfits(self.db, self.user, count=1, scenario="daily")

    self.assertEqual(len(outfits), 1)
    self.assertIn(OUTFIT_ENGINE_VERSION, outfits[0].reason)

  def test_skip_is_not_hard_excluded_from_outfit_candidates(self) -> None:
    skipped_shoe = next(product for product in self.products if product.category == "обувь")
    for product in self.products:
      if product.category == "обувь" and product.id != skipped_shoe.id:
        product.is_active = 0
    self.db.add(
      RecommendationEventV2(
        id=str(uuid4()),
        user_id=self.user.id,
        product_id=skipped_shoe.id,
        event_type="skip",
        event_weight=-0.5,
        meta_json={},
        created_at=datetime.now(timezone.utc),
      )
    )
    self.db.flush()

    outfits = generate_outfits_v2(self.db, self.user, count=1, scenario="daily")

    self.assertEqual(len(outfits), 1)
    self.assertIn(skipped_shoe.id, set(outfits[0].items_json.values()))

  def test_wrapper_records_metric_when_v2_falls_back(self) -> None:
    with patch("app.services.outfit_engine_v2.generate_outfits_v2", side_effect=RuntimeError("boom")):
      outfits = generate_outfits(self.db, self.user, count=1, scenario="daily")

    self.assertEqual(len(outfits), 1)
    self.assertIn("legacy_outfit_service", outfits[0].reason)
    metric = self.db.execute(
      select(MetricEvent).where(MetricEvent.name == "outfit_engine_v2_failure")
    ).scalar_one()
    self.assertEqual(metric.user_id, self.user.id)
    self.assertEqual(metric.meta_json["engine"], OUTFIT_ENGINE_VERSION)
    self.assertEqual(metric.meta_json["fallback_engine"], "legacy_outfit_service")
    self.assertEqual(metric.meta_json["error_type"], "RuntimeError")

  def test_compatibility_rewards_consistent_items(self) -> None:
    shirt = _product(title="Black minimal shirt", category="рубашки")
    pants = _product(title="Black minimal trousers", category="брюки")
    shoes = _product(title="Black minimal shoes", category="обувь")
    clash = _product(title="Neon sport shoes", category="обувь", color="neon", style="sport")
    consistent_items = [
      SlotCandidate("top", shirt, 0.8, 0.8, 0.8, 0.8),
      SlotCandidate("bottom", pants, 0.8, 0.8, 0.8, 0.8),
      SlotCandidate("shoes", shoes, 0.8, 0.8, 0.8, 0.8),
    ]
    clashing_items = [
      SlotCandidate("top", shirt, 0.8, 0.8, 0.8, 0.8),
      SlotCandidate("bottom", pants, 0.8, 0.8, 0.8, 0.8),
      SlotCandidate("shoes", clash, 0.8, 0.8, 0.8, 0.8),
    ]

    consistent, _ = compatibility_score(consistent_items, scenario="daily")
    clashing, _ = compatibility_score(clashing_items, scenario="daily")

    self.assertGreater(consistent, clashing)


if __name__ == "__main__":
  unittest.main()
