from __future__ import annotations

import math
import unittest

from app.evaluation.ranking_metrics import (
  both_at_k,
  dislike_leakage_at_k,
  fit_pass_rate_at_k,
  hit_rate_at_k,
  mrr,
  negative_rate_at_k,
  ndcg_at_k,
  precision_at_k,
  recall_at_k,
)


class RankingMetricsTests(unittest.TestCase):
  def test_core_metrics_at_k(self) -> None:
    ranked = ["a", "b", "c", "d"]
    relevant = {"b", "d", "z"}

    self.assertAlmostEqual(precision_at_k(ranked, relevant, 3), 1 / 3)
    self.assertAlmostEqual(recall_at_k(ranked, relevant, 3), 1 / 3)
    self.assertEqual(hit_rate_at_k(ranked, relevant, 3), 1.0)
    self.assertAlmostEqual(mrr(ranked, relevant), 0.5)

    expected_dcg = 1 / math.log2(3)
    expected_idcg = 1 + (1 / math.log2(3)) + (1 / math.log2(4))
    self.assertAlmostEqual(ndcg_at_k(ranked, relevant, 3), expected_dcg / expected_idcg)

  def test_fit_and_both_metrics(self) -> None:
    ranked = ["p1", "p2", "p3"]

    self.assertAlmostEqual(
      fit_pass_rate_at_k(
        ranked,
        fit_positive_ids={"p1", "p3"},
        fit_negative_ids=set(),
        k=3,
      ),
      2 / 3,
    )
    self.assertAlmostEqual(
      fit_pass_rate_at_k(
        ranked,
        fit_positive_ids=set(),
        fit_negative_ids={"p2"},
        k=3,
      ),
      2 / 3,
    )
    self.assertAlmostEqual(
      both_at_k(ranked, taste_positive_ids={"p1", "p2"}, fit_positive_ids={"p1", "p3"}, k=3),
      1 / 3,
    )

  def test_negative_metrics(self) -> None:
    ranked = ["p1", "bad", "p3"]

    self.assertAlmostEqual(negative_rate_at_k(ranked, {"bad"}, 3), 1 / 3)
    self.assertEqual(dislike_leakage_at_k(ranked, {"bad"}, 3), 1.0)
    self.assertEqual(dislike_leakage_at_k(ranked, {"bad"}, 1), 0.0)


if __name__ == "__main__":
  unittest.main()
