from __future__ import annotations

import unittest
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import (
  FitProfile,
  Product,
  ProductEmbedding,
  RecommendationEventV2,
  TasteProfile,
  User,
)
from app.services.candidate_retrieval_service import retrieve_candidates
from app.services.embedding_service import (
  DEFAULT_VECTOR_STORE,
  build_query_embedding,
  build_user_taste_embedding,
  index_active_product_embeddings,
  product_embedding_text,
  retrieve_embedding_candidates,
  upsert_product_embedding,
)


def _product(*, title: str, category: str = "худи", style: str = "streetwear") -> Product:
  return Product(
    id=str(uuid4()),
    external_id=str(uuid4()),
    source="test-shop",
    title=title,
    brand="test-brand",
    category=category,
    subcategory="sweatshirts",
    price=3990,
    image_url="https://example.test/image.jpg",
    product_url="https://example.test/product",
    affiliate_url="https://example.test/affiliate",
    description="black oversized cotton casual relaxed fit",
    available_sizes=["M"],
    colors=["black"],
    color_family="black",
    material="cotton",
    occasion="daily",
    gender_target="womenswear",
    fit="oversized",
    silhouette="relaxed",
    style_tags=[style],
    is_available=1,
    is_active=1,
    is_deleted_from_feed=0,
  )


class EmbeddingServiceTests(unittest.TestCase):
  def setUp(self) -> None:
    self.engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(self.engine)
    self.Session = sessionmaker(bind=self.engine, autoflush=False, future=True)
    self.db = self.Session()
    now = datetime.now(timezone.utc)
    self.user = User(id=str(uuid4()), email="embedding@test.io", password_hash="x")
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
    self.db.flush()

  def tearDown(self) -> None:
    self.db.close()
    self.engine.dispose()

  def test_product_embedding_text_contains_retrieval_fields(self) -> None:
    product = _product(title="Black oversized cotton sweatshirt")

    text = product_embedding_text(product)

    self.assertIn("test-brand", text)
    self.assertIn("black oversized cotton sweatshirt", text)
    self.assertIn("худи", text)
    self.assertIn("streetwear", text)
    self.assertIn("relaxed", text)
    self.assertIn("daily", text)

  def test_sql_vector_store_upsert_search_delete(self) -> None:
    product = _product(title="Black oversized cotton sweatshirt")
    self.db.add(product)
    self.db.flush()
    row = upsert_product_embedding(self.db, product)

    hits = DEFAULT_VECTOR_STORE.search_similar_products(
      self.db,
      query_embedding=list(row.embedding_json),
      limit=5,
    )

    self.assertEqual(hits[0].product_id, product.id)
    DEFAULT_VECTOR_STORE.delete_product_embedding(self.db, product_id=product.id)
    self.assertIsNone(
      self.db.execute(select(ProductEmbedding).where(ProductEmbedding.product_id == product.id)).scalar_one_or_none()
    )

  def test_user_taste_embedding_uses_positive_and_negative_events(self) -> None:
    liked = _product(title="Black oversized cotton sweatshirt")
    disliked = _product(title="Red formal satin dress", category="платья", style="formal")
    self.db.add_all([liked, disliked])
    self.db.flush()
    upsert_product_embedding(self.db, liked)
    upsert_product_embedding(self.db, disliked)
    now = datetime.now(timezone.utc)
    self.db.add_all(
      [
        RecommendationEventV2(
          id=str(uuid4()),
          user_id=self.user.id,
          product_id=liked.id,
          event_type="save",
          event_weight=4.0,
          meta_json={},
          created_at=now,
        ),
        RecommendationEventV2(
          id=str(uuid4()),
          user_id=self.user.id,
          product_id=disliked.id,
          event_type="dislike",
          event_weight=-3.0,
          meta_json={},
          created_at=now,
        ),
      ]
    )
    self.db.flush()

    user_embedding = build_user_taste_embedding(self.db, user_id=self.user.id)
    query = build_query_embedding(user_embedding)

    self.assertEqual(user_embedding.positive_events, 1)
    self.assertEqual(user_embedding.negative_events, 1)
    self.assertIsNotNone(query)

  def test_skip_is_not_used_in_negative_centroid(self) -> None:
    skipped = _product(title="Maybe later cotton sweatshirt")
    self.db.add(skipped)
    self.db.flush()
    upsert_product_embedding(self.db, skipped)
    self.db.add(
      RecommendationEventV2(
        id=str(uuid4()),
        user_id=self.user.id,
        product_id=skipped.id,
        event_type="skip",
        event_weight=-0.5,
        meta_json={},
        created_at=datetime.now(timezone.utc),
      )
    )
    self.db.flush()

    user_embedding = build_user_taste_embedding(self.db, user_id=self.user.id)

    self.assertEqual(user_embedding.negative_events, 0)
    self.assertIsNone(user_embedding.negative_centroid)

  def test_batch_indexes_active_catalog_embeddings(self) -> None:
    active = _product(title="Active black oversized hoodie")
    demo = _product(title="Demo black oversized hoodie")
    demo.source = "demo"
    inactive = _product(title="Inactive black oversized hoodie")
    inactive.is_active = 0
    self.db.add_all([active, demo, inactive])
    self.db.flush()

    summary = index_active_product_embeddings(self.db)

    indexed_ids = {
      row.product_id
      for row in self.db.execute(select(ProductEmbedding)).scalars().all()
    }
    self.assertEqual(summary.scanned, 1)
    self.assertEqual(summary.indexed, 1)
    self.assertIn(active.id, indexed_ids)
    self.assertNotIn(demo.id, indexed_ids)
    self.assertNotIn(inactive.id, indexed_ids)

  def test_embedding_retrieval_adds_candidate_source(self) -> None:
    liked = _product(title="Black oversized cotton sweatshirt")
    similar = _product(title="Black oversized cotton hoodie")
    unrelated = _product(title="Red formal satin dress", category="платья", style="formal")
    self.db.add_all([liked, similar, unrelated])
    self.db.flush()
    for product in (liked, similar, unrelated):
      upsert_product_embedding(self.db, product)
    self.db.add(
      RecommendationEventV2(
        id=str(uuid4()),
        user_id=self.user.id,
        product_id=liked.id,
        event_type="save",
        event_weight=4.0,
        meta_json={},
        created_at=datetime.now(timezone.utc),
      )
    )
    self.db.flush()

    rows = retrieve_embedding_candidates(self.db, user=self.user, limit=5)
    ids = [product.id for product in rows]
    self.assertIn(similar.id, ids)

    fit = self.db.execute(select(FitProfile).where(FitProfile.user_id == self.user.id)).scalar_one()
    taste = self.db.execute(select(TasteProfile).where(TasteProfile.user_id == self.user.id)).scalar_one()
    result = retrieve_candidates(
      self.db,
      user=self.user,
      fit=fit,
      taste=taste,
      taste_features={},
      max_candidates=50,
    )

    self.assertIn("embedding", result.sources_by_product_id.get(similar.id, set()))


if __name__ == "__main__":
  unittest.main()
