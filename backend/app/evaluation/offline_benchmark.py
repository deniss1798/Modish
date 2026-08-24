"""Offline benchmark runner for product recommendation rankings."""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from ..services.recommendation_config import (
  ALGORITHM_VERSION,
  DEFAULT_RANKING_ALGORITHM,
  recommendation_algorithm_metadata,
)
from .ranking_metrics import (
  both_at_k,
  fit_pass_rate_at_k,
  hit_rate_at_k,
  mrr,
  ndcg_at_k,
  precision_at_k,
  recall_at_k,
)


def _id_set(values: Iterable[object] | None) -> frozenset[str]:
  if values is None:
    return frozenset()
  if isinstance(values, str):
    values = [values]
  try:
    iterator = iter(values)
  except TypeError:
    iterator = iter([values])
  return frozenset(str(v or "").strip() for v in iterator if str(v or "").strip())


def _mean(values: Iterable[float | None]) -> float:
  clean = [float(v) for v in values if v is not None]
  if not clean:
    return 0.0
  return sum(clean) / float(len(clean))


@dataclass(frozen=True)
class BenchmarkCase:
  user_id: str
  positive_product_ids: frozenset[str] = field(default_factory=frozenset)
  negative_product_ids: frozenset[str] = field(default_factory=frozenset)
  neutral_product_ids: frozenset[str] = field(default_factory=frozenset)
  fit_positive_product_ids: frozenset[str] = field(default_factory=frozenset)
  fit_negative_product_ids: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class BenchmarkResult:
  algorithm_version: str
  ranking_algorithm: str
  k: int
  cases_count: int
  metrics: dict[str, float]
  algorithm_config: dict = field(default_factory=dict)

  def to_dict(self) -> dict:
    return {
      "algorithm_version": self.algorithm_version,
      "ranking_algorithm": self.ranking_algorithm,
      "k": self.k,
      "cases_count": self.cases_count,
      "metrics": dict(self.metrics),
      "algorithm_config": dict(self.algorithm_config),
    }


def case_from_dict(raw: Mapping[str, object]) -> BenchmarkCase:
  user_id = str(raw.get("user_id") or "").strip()
  if not user_id:
    raise ValueError("benchmark case requires user_id")
  return BenchmarkCase(
    user_id=user_id,
    positive_product_ids=_id_set(raw.get("positive_product_ids")),
    negative_product_ids=_id_set(raw.get("negative_product_ids")),
    neutral_product_ids=_id_set(raw.get("neutral_product_ids")),
    fit_positive_product_ids=_id_set(raw.get("fit_positive_product_ids")),
    fit_negative_product_ids=_id_set(raw.get("fit_negative_product_ids")),
  )


def load_benchmark_cases(path: str | Path) -> list[BenchmarkCase]:
  source = Path(path)
  text = source.read_text(encoding="utf-8").strip()
  if not text:
    return []

  if source.suffix.lower() == ".jsonl":
    rows = [json.loads(line) for line in text.splitlines() if line.strip()]
  else:
    parsed = json.loads(text)
    rows = parsed if isinstance(parsed, list) else [parsed]
  return [case_from_dict(row) for row in rows if isinstance(row, Mapping)]


def evaluate_rankings(
  cases: Iterable[BenchmarkCase],
  rankings_by_user_id: Mapping[str, Iterable[object]],
  *,
  k: int = 10,
  algorithm_version: str = ALGORITHM_VERSION,
  ranking_algorithm: str = DEFAULT_RANKING_ALGORITHM,
  algorithm_config: Mapping[str, object] | None = None,
) -> BenchmarkResult:
  benchmark_cases = list(cases)
  k = max(1, int(k))

  precision_values: list[float] = []
  recall_values: list[float] = []
  hit_values: list[float] = []
  mrr_values: list[float] = []
  ndcg_values: list[float] = []
  taste_hit_values: list[float] = []
  fit_pass_values: list[float | None] = []
  both_rate_values: list[float] = []
  both_share_values: list[float] = []

  for case in benchmark_cases:
    ranked = list(rankings_by_user_id.get(case.user_id, []))
    positives = case.positive_product_ids
    both_positive = case.positive_product_ids & case.fit_positive_product_ids

    precision_values.append(precision_at_k(ranked, positives, k))
    recall_values.append(recall_at_k(ranked, positives, k))
    hit_values.append(hit_rate_at_k(ranked, positives, k))
    mrr_values.append(mrr(ranked, positives))
    ndcg_values.append(ndcg_at_k(ranked, positives, k))
    taste_hit_values.append(hit_rate_at_k(ranked, positives, k))
    fit_pass_values.append(
      fit_pass_rate_at_k(
        ranked,
        fit_positive_ids=case.fit_positive_product_ids,
        fit_negative_ids=case.fit_negative_product_ids,
        k=k,
      )
    )
    both_rate_values.append(hit_rate_at_k(ranked, both_positive, k))
    both_share_values.append(both_at_k(ranked, taste_positive_ids=positives, fit_positive_ids=case.fit_positive_product_ids, k=k))

  config = dict(algorithm_config or recommendation_algorithm_metadata())
  metrics = {
    f"Precision@{k}": _mean(precision_values),
    f"Recall@{k}": _mean(recall_values),
    f"HitRate@{k}": _mean(hit_values),
    "MRR": _mean(mrr_values),
    f"NDCG@{k}": _mean(ndcg_values),
    f"TasteHitRate@{k}": _mean(taste_hit_values),
    f"FitPassRate@{k}": _mean(fit_pass_values),
    f"BothRate@{k}": _mean(both_rate_values),
    f"Both@{k}": _mean(both_share_values),
  }
  return BenchmarkResult(
    algorithm_version=algorithm_version,
    ranking_algorithm=ranking_algorithm,
    k=k,
    cases_count=len(benchmark_cases),
    metrics={key: round(value, 6) for key, value in metrics.items()},
    algorithm_config=config,
  )


def compare_rankings(
  cases: Iterable[BenchmarkCase],
  rankings_by_algorithm: Mapping[str, Mapping[str, Iterable[object]]],
  *,
  k: int = 10,
) -> dict[str, BenchmarkResult]:
  benchmark_cases = list(cases)
  return {
    ranking_algorithm: evaluate_rankings(
      benchmark_cases,
      rankings,
      k=k,
      algorithm_version=ALGORITHM_VERSION,
      ranking_algorithm=ranking_algorithm,
      algorithm_config=recommendation_algorithm_metadata(ranking_algorithm=ranking_algorithm),
    )
    for ranking_algorithm, rankings in rankings_by_algorithm.items()
  }
