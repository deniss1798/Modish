from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.evaluation.offline_benchmark import (
  BenchmarkCase,
  case_from_dict,
  compare_rankings,
  evaluate_rankings,
  load_benchmark_cases,
)
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
      "u1": ["p1", "p2", "p3"],
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
    self.assertGreater(result.metrics["BothRate@3"], 0.0)
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


if __name__ == "__main__":
  unittest.main()
