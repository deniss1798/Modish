from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.evaluation.offline_benchmark import (
  BenchmarkCase,
  case_from_dict,
  compare_rankings,
  evaluate_rankings,
  load_benchmark_cases,
  run_benchmark,
)
from app.models import FitProfile, Product, TasteProfile, User, UserTasteFeature
from app.services.recommendation_config import ALGORITHM_VERSION, DEFAULT_RANKING_ALGORITHM


class OfflineBenchmarkTests(unittest.TestCase):
  def test_case_from_dict_normalizes_dataset_row(self) -> None:
    case = case_from_dict(
      {
        "user_id": " u1 ",
        "positive_product_ids": ["p1", "", "p2"],
        "negative_product_ids": ["n1"],
        "neutral_product_ids": [],
        "fit_positive_product_ids": ["p1"],
        "fit_negative_product_ids": ["n2"],
      }
    )

    self.assertEqual(case.user_id, "u1")
    self.assertEqual(case.positive_product_ids, frozenset({"p1", "p2"}))
    self.assertEqual(case.fit_positive_product_ids, frozenset({"p1"}))

  def test_load_benchmark_cases_from_json(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      path = Path(tmp) / "benchmark.json"
      path.write_text(json.dumps([{"user_id": "u1", "positive_product_ids": ["p1"]}]), encoding="utf-8")

      cases = load_benchmark_cases(path)

    self.assertEqual(len(cases), 1)
    self.assertEqual(cases[0].user_id, "u1")

  def test_evaluate_rankings_reports_core_and_fit_metrics(self) -> None:
    cases = [
      BenchmarkCase(
        user_id="u1",
        positive_product_ids=frozenset({"p2", "p4"}),
        negative_product_ids=frozenset({"bad"}),
        fit_positive_product_ids=frozenset({"p2", "p3"}),
        fit_negative_product_ids=frozenset({"bad"}),
      ),
      BenchmarkCase(
        user_id="u2",
        positive_product_ids=frozenset({"x1"}),
        fit_positive_product_ids=frozenset({"x1"}),
      ),
    ]
    rankings = {
      "u1": ["p1", "p2", "bad"],
      "u2": ["z9", "x1"],
    }

    result = evaluate_rankings(cases, rankings, k=3)

    self.assertEqual(result.algorithm_version, ALGORITHM_VERSION)
    self.assertEqual(result.ranking_algorithm, DEFAULT_RANKING_ALGORITHM)
    self.assertEqual(result.cases_count, 2)
    self.assertIn("Precision@3", result.metrics)
    self.assertIn("Recall@3", result.metrics)
    self.assertIn("MRR", result.metrics)
    self.assertIn("NDCG@3", result.metrics)
    self.assertIn("TasteHitRate@3", result.metrics)
    self.assertIn("FitPassRate@3", result.metrics)
    self.assertIn("BothRate@3", result.metrics)
    self.assertIn("Both@3", result.metrics)
    self.assertIn("NegativeRate@3", result.metrics)
    self.assertIn("DislikeLeakage@3", result.metrics)
    self.assertGreater(result.metrics["BothRate@3"], 0.0)
    self.assertGreater(result.metrics["NegativeRate@3"], 0.0)
    self.assertIn("final_score_weights", result.algorithm_config)

  def test_compare_rankings_keeps_algorithm_labels(self) -> None:
    cases = [BenchmarkCase(user_id="u1", positive_product_ids=frozenset({"p1"}))]

    results = compare_rankings(
      cases,
      {
        "ranking_v3": {"u1": ["p2", "p1"]},
        "ranking_v4": {"u1": ["p1", "p2"]},
      },
      k=1,
    )

    self.assertEqual(set(results), {"ranking_v3", "ranking_v4"})
    self.assertLess(results["ranking_v3"].metrics["HitRate@1"], results["ranking_v4"].metrics["HitRate@1"])

  def test_run_benchmark_executes_recommendation_pipeline(self) -> None:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, future=True)
    db = Session()
    try:
      now = datetime.now(timezone.utc)
      user = User(id=str(uuid4()), email="benchmark@test.io", password_hash="x")
      db.add(user)
      db.add(
        FitProfile(
          id=str(uuid4()),
          user_id=user.id,
          height_cm=170,
          gender_target="womenswear",
          clothing_size="M",
          budget_min=0,
          budget_max=10_000,
          interest_categories=[],
          style_scenarios=["daily"],
        )
      )
      db.add(
        TasteProfile(
          id=str(uuid4()),
          user_id=user.id,
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
      positive = _benchmark_product(
        title="Old perfect minimal dress",
        category="платья",
        updated_at=now - timedelta(days=700),
      )
      recent = _benchmark_product(
        title="Recent neutral shirt",
        category="рубашки",
        updated_at=now,
      )
      db.add_all([positive, recent])
      db.add(
        UserTasteFeature(
          id=str(uuid4()),
          user_id=user.id,
          feature_type="category",
          feature_value="платья",
          preference_score=1.0,
          confidence=1.0,
          positive_count=10,
          negative_count=0,
          last_positive_at=now,
          last_signal_at=now,
          last_decay_at=now,
          created_at=now,
          updated_at=now,
        )
      )
      db.flush()
      cases = [
        BenchmarkCase(
          user_id=user.id,
          positive_product_ids=frozenset({positive.id}),
          negative_product_ids=frozenset({recent.id}),
          fit_positive_product_ids=frozenset({positive.id, recent.id}),
        )
      ]

      v3 = run_benchmark(db, cases, algorithm="ranking_v3", k=1)
      v4 = run_benchmark(db, cases, algorithm="ranking_v4", k=1)

      self.assertLess(v3.metrics["HitRate@1"], v4.metrics["HitRate@1"])
      self.assertLess(v4.metrics["NegativeRate@1"], v3.metrics["NegativeRate@1"])
    finally:
      db.close()
      engine.dispose()


def _benchmark_product(*, title: str, category: str, updated_at: datetime) -> Product:
  return Product(
    id=str(uuid4()),
    external_id=str(uuid4()),
    source="test-shop",
    title=title,
    brand="test-brand",
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


if __name__ == "__main__":
  unittest.main()
