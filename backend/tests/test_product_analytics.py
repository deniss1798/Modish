from __future__ import annotations

import unittest
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import ProductImpression, User
from app.services.product_analytics_service import log_feed_impressions
from app.services.recommendation_config import ALGORITHM_VERSION


class ProductAnalyticsTests(unittest.TestCase):
  def setUp(self) -> None:
    self.engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(self.engine)
    self.Session = sessionmaker(bind=self.engine, autoflush=False, future=True)
    self.db = self.Session()
    self.user = User(id=str(uuid4()), email="analytics@test.io", password_hash="x")
    self.db.add(self.user)
    self.db.flush()

  def tearDown(self) -> None:
    self.db.close()

  def test_log_feed_impressions_stores_ranking_metadata(self) -> None:
    product_id = str(uuid4())

    log_feed_impressions(
      self.db,
      user_id=self.user.id,
      product_ids=[product_id],
      ranking_metadata={
        product_id: {
          "algorithm_version": ALGORITHM_VERSION,
          "ranking_algorithm": "ranking_v4",
          "candidate_sources": ["taste", "fit"],
          "fit_score": 0.9,
          "taste_score": 0.8,
          "context_score": 0.7,
          "quality_score": 0.6,
          "exploration_score": 0.1,
          "final_score": 0.82,
          "rank": 1,
        }
      },
    )
    self.db.flush()

    row = self.db.execute(select(ProductImpression)).scalar_one()
    self.assertEqual(row.algorithm_version, ALGORITHM_VERSION)
    self.assertEqual(row.candidate_source, "taste,fit")
    self.assertAlmostEqual(row.final_score or 0.0, 0.82)
    self.assertEqual(row.rank_position, 1)
    self.assertEqual(row.ranking_meta_json["fit_score"], 0.9)


if __name__ == "__main__":
  unittest.main()
