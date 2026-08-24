from __future__ import annotations

import unittest
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import FitProfile, Product, User
from app.services.recommendation_engine import expand_product_exclusions, generate_feed


def _product(
  *,
  product_id: str,
  external_id: str,
  source: str,
  group_id: str | None,
  title: str,
) -> Product:
  return Product(
    id=product_id,
    external_id=external_id,
    source=source,
    title=title,
    brand="test-brand",
    category="shirt",
    price=1990,
    image_url="https://example.test/image.jpg",
    product_url="https://example.test/product",
    affiliate_url="https://example.test/affiliate",
    group_id=group_id,
    available_sizes=["M"],
    colors=["white"],
    gender_target="womenswear",
    style_tags=["minimalism"],
    is_available=1,
    is_active=1,
    is_deleted_from_feed=0,
  )


class RecommendationExclusionTests(unittest.TestCase):
  def setUp(self) -> None:
    self.engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(self.engine)
    self.Session = sessionmaker(bind=self.engine, autoflush=False, future=True)
    self.db = self.Session()
    self.user = User(id=str(uuid4()), email="feed-exclusion@test.io", password_hash="x")
    self.db.add(self.user)
    self.db.flush()
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
        style_scenarios=["minimalism"],
      )
    )
    self.p1 = _product(
      product_id=str(uuid4()),
      external_id="offer-1",
      source="shop-a",
      group_id="group-1",
      title="White shirt size M",
    )
    self.p2 = _product(
      product_id=str(uuid4()),
      external_id="offer-2",
      source="shop-a",
      group_id="group-1",
      title="White shirt size L",
    )
    self.p3 = _product(
      product_id=str(uuid4()),
      external_id="offer-3",
      source="shop-b",
      group_id="group-1",
      title="Other shop shirt",
    )
    self.p4 = _product(
      product_id=str(uuid4()),
      external_id="offer-4",
      source="shop-a",
      group_id="group-2",
      title="Different shirt",
    )
    self.db.add_all([self.p1, self.p2, self.p3, self.p4])
    self.db.flush()

  def tearDown(self) -> None:
    self.db.close()

  def test_exclusions_expand_to_same_source_group_variants(self) -> None:
    hidden = expand_product_exclusions(self.db, {self.p1.id})

    self.assertIn(self.p1.id, hidden)
    self.assertIn(self.p2.id, hidden)
    self.assertNotIn(self.p3.id, hidden)
    self.assertNotIn(self.p4.id, hidden)

  def test_generate_feed_does_not_return_same_group_variant(self) -> None:
    feed = generate_feed(self.db, self.user, limit=10, exclude_product_ids={self.p1.id})
    ids = {item.product.id for item in feed}

    self.assertNotIn(self.p1.id, ids)
    self.assertNotIn(self.p2.id, ids)
    self.assertIn(self.p4.id, ids)


if __name__ == "__main__":
  unittest.main()
