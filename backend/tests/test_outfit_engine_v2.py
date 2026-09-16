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
    image_url=f"https://example.test/{uuid4()}.jpg",
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

  def test_wrapper_does_not_replace_failed_engine_with_unsafe_outfits(self) -> None:
    with patch("app.services.outfit_engine_v2.generate_outfits_v2", side_effect=RuntimeError("boom")):
      with self.assertRaises(RuntimeError):
        generate_outfits(self.db, self.user, count=1, scenario="daily")

    metric = self.db.execute(
      select(MetricEvent).where(MetricEvent.name == "outfit_engine_v2_failure")
    ).scalar_one()
    self.assertEqual(metric.user_id, self.user.id)
    self.assertEqual(metric.meta_json["engine"], OUTFIT_ENGINE_VERSION)
    self.assertEqual(metric.meta_json["fallback_engine"], "legacy_outfit_service")
    self.assertEqual(metric.meta_json["error_type"], "RuntimeError")

  def test_empty_v2_does_not_fall_back_to_incomplete_outfit(self) -> None:
    for p in self.products:
      if p.category == "обувь":
        p.is_active = 0
    self.db.flush()
    self.assertEqual(generate_outfits(self.db, self.user, count=4), [])

  def test_outfits_change_at_least_two_items_and_have_no_accessory(self) -> None:
    from app.services.product_identity import product_identity_keys
    outfits = generate_outfits(self.db, self.user, count=4)
    self.assertGreaterEqual(len(outfits), 2)
    by_id = {p.id: p for p in self.products}
    for i, outfit in enumerate(outfits):
      self.assertNotIn("accessory", outfit.items_json)
      keys = set().union(*(product_identity_keys(by_id[pid]) for pid in outfit.items_json.values()))
      for other in outfits[:i]:
        self.assertLessEqual(sum(bool(product_identity_keys(by_id[pid]) & keys) for pid in other.items_json.values()), 1)

  def test_mislabelled_underwear_swimwear_and_slippers_never_form_outfits(self) -> None:
    from app.services.outfit_quality import scenario_product_ok
    for title, category in [("Трусы Calvin Klein", "брюки"), ("Термобелье низ", "брюки"),
                            ("Шорты для плавания", "брюки"), ("Тапочки домашние", "обувь"),
                            ("Брюки пижамные", "брюки"), ("Swim shorts", "шорты"),
                            ("Боксерки Green Hill", "обувь"), ("Куртка BAON", "верхний_слой")]:
      with self.subTest(title=title):
        p = _product(title=title, category=category)
        self.assertFalse(scenario_product_ok(p, "daily"))
        self.assertFalse(scenario_product_ok(p, "office"))
    p = _product(title="Брюки", category="брюки")
    p.merchant_category = "Белье / Кальсоны"
    self.assertFalse(scenario_product_ok(p, "daily"))

  def test_formal_scenarios_reject_sports_and_casual_only_items(self) -> None:
    from app.services.outfit_quality import scenario_product_ok
    for title in ["Кроссовки Nike", "Поло", "Футболка", "Брюки спортивные", "Training shoes", "Легинсы Demix"]:
      for scenario in ["office", "evening"]:
        self.assertFalse(scenario_product_ok(_product(title=title, category="рубашки"), scenario))

  def test_explicit_gender_in_title_overrides_incorrect_feed_label(self) -> None:
    from app.services.catalog.rule_filters import product_gender_compatible
    p = _product(title="Мужские ботинки", category="обувь")
    self.assertFalse(product_gender_compatible(p, "womenswear"))
    p.gender_target = "unisex"
    self.assertFalse(product_gender_compatible(p, "womenswear"))
    p.title = "Топ-бандо однотонный"
    self.assertFalse(product_gender_compatible(p, "menswear"))
    p.title = "Туфли"
    p.category = "женщинам/обувь"
    self.assertFalse(product_gender_compatible(p, "menswear"))

  def test_catalog_edge_cases_are_rejected(self) -> None:
    from app.services.outfit_quality import scenario_product_ok
    for title, category, scenario in [("Балетки для девочек", "обувь", "office"),
       ("Рубашка для мальчиков", "рубашки", "daily"),
       ("Ботинки с теплоизоляционной подкладкой", "обувь", "office"),
       ("Брюки из трикотажа", "брюки", "evening")]:
      self.assertFalse(scenario_product_ok(_product(title=title, category=category), scenario))

  def test_unknown_gender_is_not_guessed_in_an_outfit(self) -> None:
    from app.services.outfit_engine_v2 import _hard_ok
    fit = self.db.execute(select(FitProfile).where(FitProfile.user_id == self.user.id)).scalar_one()
    p = _product(title="Туфли", category="обувь")
    p.gender_target = None
    self.assertFalse(_hard_ok(p, fit=fit, rules_by_source={}))

  def test_other_scenarios_do_not_exhaust_all_shoes(self) -> None:
    rows = [generate_outfits(self.db,self.user,count=1,scenario=scenario) for scenario in ("daily","office","evening")]
    self.assertEqual([len(row) for row in rows], [1,1,1])
    self.assertEqual(len({tuple(sorted(row[0].items_json.values())) for row in rows}), 3)

  def test_combination_rejects_conflicting_seasons_and_multiple_accents(self) -> None:
    from app.services.outfit_engine_v2 import _build_combinations
    products = [_product(title="Shirt",category="рубашки",color="red"),
                _product(title="Trousers",category="брюки",color="green"),
                _product(title="Shoes",category="обувь",color="black")]
    buckets = {slot:[SlotCandidate(slot,p,.8,.8,.8,.8)] for slot,p in zip(["top","bottom","shoes"],products)}
    self.assertEqual(_build_combinations(buckets,scenario="daily",fit=None,limit=3), [])
    products[1].colors = ["cream"]
    products[1].color_family = "cream"
    self.assertTrue(_build_combinations(buckets,scenario="daily",fit=None,limit=3))
    products[0].season = "winter"
    products[2].season = "summer"
    self.assertEqual(_build_combinations(buckets,scenario="daily",fit=None,limit=3), [])

  def test_old_invalid_and_duplicate_outfits_hidden_without_deleting_saved(self) -> None:
    from app.api.outfits import visible_outfits
    from app.models import Outfit
    rows = generate_outfits(self.db, self.user, count=1)
    valid = rows[0]
    duplicate = Outfit(id=str(uuid4()), user_id=self.user.id, items_json=dict(valid.items_json),
                       total_price=valid.total_price, style_direction="daily", is_saved=1,
                       created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
    self.db.add(duplicate)
    self.db.flush()
    self.assertEqual(len(visible_outfits(self.db, self.user, [valid, duplicate])), 1)
    self.assertIsNotNone(self.db.get(Outfit, duplicate.id))
    by_id = {p.id: p for p in self.products}
    by_id[valid.items_json["bottom"]].title = "Трусы"
    self.assertEqual(visible_outfits(self.db, self.user, [valid, duplicate]), [])

  def test_refresh_rotates_garments_across_three_batches(self) -> None:
    from app.services.product_identity import product_identity_keys
    for category, title in [("рубашки", "Shirt"), ("брюки", "Trousers"), ("обувь", "Shoes")]:
      self.db.add_all(_product(title=f"{title} {i}", category=category, price=1500) for i in range(12))
    self.db.flush()
    seen = set()
    for _ in range(3):
      rows = generate_outfits_v2(self.db, self.user, count=3)
      self.assertEqual(len(rows), 3)
      for row in rows:
        keys = set().union(*(product_identity_keys(self.db.get(Product, pid)) for pid in row.items_json.values()))
        self.assertFalse(keys & seen)
        seen.update(keys)
      self.db.commit()

  def test_exhausted_refresh_keeps_current_and_saved_outfits(self) -> None:
    from app.models import Outfit
    for p in self.products:
      if p.title.startswith("White") or p.title.startswith("Gray"):
        p.is_active = 0
    self.db.flush()
    first = generate_outfits_v2(self.db, self.user, count=1)[0]
    self.assertEqual(generate_outfits_v2(self.db, self.user, count=1), [])
    self.assertIsNotNone(self.db.get(Outfit, first.id))
    first.is_saved = 1
    self.db.flush()
    self.assertEqual(generate_outfits_v2(self.db, self.user, count=1), [])
    self.assertEqual(self.db.get(Outfit, first.id).is_saved, 1)

  def test_new_offer_ids_with_same_images_do_not_bypass_history(self) -> None:
    first = generate_outfits_v2(self.db, self.user, count=2)
    for pid in {pid for row in first for pid in row.items_json.values()}:
      old = self.db.get(Product, pid)
      alias = _product(title=old.title, category=old.category)
      alias.image_url = old.image_url
      old.is_active = 0
      self.db.add(alias)
    self.db.flush()
    self.assertEqual(generate_outfits_v2(self.db, self.user, count=2), [])

  def test_rotation_history_does_not_affect_another_user(self) -> None:
    generate_outfits_v2(self.db, self.user, count=2)
    other = User(id=str(uuid4()), email="other@test.io", password_hash="x")
    self.db.add(other)
    self.db.add(FitProfile(id=str(uuid4()), user_id=other.id, height_cm=170, gender_target="womenswear", clothing_size="M", budget_max=10000))
    self.db.flush()
    self.assertTrue(generate_outfits_v2(self.db, other, count=2))

  def test_scarce_shoes_allow_new_top_and_bottom(self) -> None:
    for p in self.products:
      if p.title == "White leather shoes": p.is_active = 0
    self.db.flush()
    first = generate_outfits_v2(self.db, self.user, count=1)[0]
    second = generate_outfits_v2(self.db, self.user, count=1)[0]
    self.assertEqual(first.items_json['shoes'], second.items_json['shoes'])
    self.assertNotEqual(first.items_json['top'], second.items_json['top'])
    self.assertNotEqual(first.items_json['bottom'], second.items_json['bottom'])
    self.assertEqual(generate_outfits_v2(self.db, self.user, count=1), [])

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
