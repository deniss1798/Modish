from __future__ import annotations

import unittest
from uuid import uuid4

from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.api.deps import create_access_token
from app.api.recommendations import EventRequest, recommendations_events
from app.db import Base
from app.models import FitProfile, Product, RecommendationEventV2, TasteProfile, User, UserProductState
from app.services.recommendation_config import event_weight, normalize_event_type
from app.services.recommendation_engine import ensure_taste_profile, score_product


def _product(*, title: str = "White minimal shirt", product_id: str | None = None) -> Product:
  return Product(
    id=product_id or str(uuid4()),
    external_id=str(uuid4()),
    source="test-shop",
    title=title,
    brand="test-brand",
    category="рубашки",
    price=2990,
    image_url="https://example.test/image.jpg",
    product_url="https://example.test/product",
    affiliate_url="https://example.test/affiliate",
    available_sizes=["M"],
    colors=["white"],
    gender_target="womenswear",
    style_tags=["minimalism"],
    is_available=1,
    is_active=1,
    is_deleted_from_feed=0,
  )


class FeedbackSemanticsTests(unittest.TestCase):
  def setUp(self) -> None:
    self.engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(self.engine)
    self.Session = sessionmaker(bind=self.engine, autoflush=False, future=True)
    self.db = self.Session()
    self.user = User(id=str(uuid4()), email="feedback@test.io", password_hash="x")
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
    self.product = _product()
    self.db.add(self.product)
    self.db.flush()
    self.credentials = HTTPAuthorizationCredentials(
      scheme="Bearer",
      credentials=create_access_token(self.user.id),
    )

  def tearDown(self) -> None:
    self.db.close()

  def _send(self, event_type: str, meta: dict | None = None) -> dict:
    return recommendations_events(
      EventRequest(event_type=event_type, product_id=self.product.id, meta=meta),
      db=self.db,
      credentials=self.credentials,
    )

  def _taste(self) -> TasteProfile:
    row = self.db.execute(
      select(TasteProfile).where(TasteProfile.user_id == self.user.id)
    ).scalar_one()
    return row

  def test_event_config_weights_and_aliases(self) -> None:
    self.assertEqual(normalize_event_type("buy_click"), "affiliate_click")
    self.assertGreater(event_weight("skip"), event_weight("dislike"))
    self.assertLess(event_weight("open_product"), event_weight("save"))
    self.assertLess(event_weight("like"), event_weight("save"))
    self.assertGreater(event_weight("purchase"), event_weight("like"))

  def test_skip_is_weak_and_not_dislike(self) -> None:
    res = self._send("skip")

    self.assertEqual(res["event_type"], "skip")
    self.assertAlmostEqual(float(res["weight"]), -0.5)

    ev = self.db.execute(select(RecommendationEventV2)).scalar_one()
    self.assertEqual(ev.event_type, "skip")
    self.assertAlmostEqual(float(ev.event_weight), -0.5)

    tp = self._taste()
    self.assertEqual(tp.disliked_categories, [])
    self.assertEqual(tp.disliked_brands, [])
    self.assertEqual(tp.disliked_colors, [])
    self.assertAlmostEqual(float(tp.category_weights["рубашки"]), -0.5)

    st = self.db.execute(select(UserProductState)).scalar_one()
    self.assertAlmostEqual(float(st.event_strength), -0.5)

  def test_dislike_reason_is_stored_without_global_color_blacklist(self) -> None:
    before = score_product(self.db, self.user, self.product).final_score
    res = self._send("dislike", {"reason": "dont_like_color"})

    self.assertEqual(res["event_type"], "dislike")
    self.assertAlmostEqual(float(res["weight"]), -3.0)
    ev = self.db.execute(select(RecommendationEventV2)).scalar_one()
    self.assertEqual(ev.meta_json["reason"], "dont_like_color")

    tp = self._taste()
    self.assertEqual(tp.disliked_colors, [])
    self.assertNotIn("рубашки", tp.disliked_categories)
    self.assertAlmostEqual(float(tp.color_weights["white"]), -3.0)
    self.assertNotIn("рубашки", tp.category_weights)

    after = score_product(self.db, self.user, self.product).final_score
    self.assertGreater(after, 0.0)
    self.assertLess(after, before)

  def test_legacy_buy_click_is_saved_as_affiliate_click(self) -> None:
    res = self._send("buy_click")

    self.assertEqual(res["event_type"], "affiliate_click")
    ev = self.db.execute(select(RecommendationEventV2)).scalar_one()
    self.assertEqual(ev.event_type, "affiliate_click")
    self.assertEqual(ev.meta_json["legacy_event_type"], "buy_click")
    self.assertAlmostEqual(float(ev.event_weight), 5.0)


if __name__ == "__main__":
  unittest.main()
