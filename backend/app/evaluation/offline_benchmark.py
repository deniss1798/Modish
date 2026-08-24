"""Offline benchmark runner for product recommendation rankings."""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from ..models import FitProfile, Product, TasteProfile, User
from ..services.recommendation_config import (
  ALGORITHM_VERSION,
  DEFAULT_RANKING_ALGORITHM,
  recommendation_algorithm_metadata,
)
from .ranking_metrics import (
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
  negative_rate_values: list[float] = []
  dislike_leakage_values: list[float] = []

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
    negative_rate_values.append(negative_rate_at_k(ranked, case.negative_product_ids, k))
    dislike_leakage_values.append(dislike_leakage_at_k(ranked, case.negative_product_ids, k))

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
    f"NegativeRate@{k}": _mean(negative_rate_values),
    f"DislikeLeakage@{k}": _mean(dislike_leakage_values),
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


def _ranking_v3_baseline(
  db: Session,
  user: User,
  *,
  limit: int,
  source: str | None = None,
  max_to_score: int = 700,
) -> list[str]:
  from ..services.catalog.catalog_quality import product_is_feed_eligible
  from ..services.catalog.rule_filters import load_rules_by_source_id, product_passes_source_rules
  from ..services.feed_filters import product_passes_hard_filters

  fit = db.execute(select(FitProfile).where(FitProfile.user_id == user.id)).scalar_one_or_none()
  taste = db.execute(select(TasteProfile).where(TasteProfile.user_id == user.id)).scalar_one_or_none()
  liked_categories = {
    str(x).strip().lower()
    for x in ((taste.liked_categories or []) if taste else [])
    if str(x).strip()
  }
  liked_brands = {
    str(x).strip().lower()
    for x in ((taste.liked_brands or []) if taste else [])
    if str(x).strip()
  }

  conds = [
    Product.is_active == 1,
    Product.is_available == 1,
    Product.is_deleted_from_feed == 0,
    Product.source != "demo",
    Product.price > 500,
    Product.image_url.isnot(None),
    Product.image_url != "",
    or_(
      and_(Product.affiliate_url.isnot(None), Product.affiliate_url != ""),
      Product.product_url != "",
    ),
  ]
  if source and source.strip():
    conds.append(Product.source == source.strip())
  rows = db.execute(
    select(Product)
    .where(and_(*conds))
    .order_by(
      Product.external_updated_at.desc(),
      Product.last_seen_in_feed_at.desc(),
      Product.updated_at.desc(),
      Product.created_at.desc(),
      Product.id.asc(),
    )
    .limit(max(50, min(1500, int(max_to_score))))
  ).scalars().all()

  rules_by_source = load_rules_by_source_id(db)
  scored: list[tuple[float, str]] = []
  for idx, product in enumerate(rows):
    if not product_is_feed_eligible(product):
      continue
    if not product_passes_source_rules(product, rules_by_source):
      continue
    if not product_passes_hard_filters(product, fit):
      continue
    score = 1.0 - (idx / max(1.0, len(rows)))
    if (product.category or "").strip().lower() in liked_categories:
      score += 0.15
    if (product.brand or "").strip().lower() in liked_brands:
      score += 0.10
    scored.append((score, product.id))
    if len(scored) >= max(limit * 5, limit):
      break
  scored.sort(key=lambda x: x[0], reverse=True)
  return [product_id for _, product_id in scored[:limit]]


def _ranking_v4_pipeline(
  db: Session,
  user: User,
  *,
  limit: int,
  source: str | None = None,
  scenario: str = "daily",
  max_to_score: int = 700,
) -> list[str]:
  from ..services.recommendation_engine import generate_feed

  items = generate_feed(
    db,
    user,
    limit=limit,
    source=source,
    scenario=scenario,
    max_to_score=max_to_score,
  )
  return [item.product.id for item in items]


def collect_rankings_from_pipeline(
  db: Session,
  cases: Iterable[BenchmarkCase],
  *,
  algorithm: str = DEFAULT_RANKING_ALGORITHM,
  k: int = 10,
  source: str | None = None,
  scenario: str = "daily",
  max_to_score: int = 700,
) -> dict[str, list[str]]:
  rankings: dict[str, list[str]] = {}
  for case in cases:
    user = db.execute(select(User).where(User.id == case.user_id)).scalar_one_or_none()
    if user is None:
      rankings[case.user_id] = []
      continue
    if algorithm == "ranking_v4":
      rankings[case.user_id] = _ranking_v4_pipeline(
        db,
        user,
        limit=k,
        source=source,
        scenario=scenario,
        max_to_score=max_to_score,
      )
    elif algorithm == "ranking_v3":
      rankings[case.user_id] = _ranking_v3_baseline(
        db,
        user,
        limit=k,
        source=source,
        max_to_score=max_to_score,
      )
    else:
      raise ValueError(f"Unsupported benchmark algorithm: {algorithm!r}")
  return rankings


def run_benchmark(
  db: Session,
  cases: Iterable[BenchmarkCase],
  *,
  algorithm: str = DEFAULT_RANKING_ALGORITHM,
  k: int = 10,
  source: str | None = None,
  scenario: str = "daily",
  max_to_score: int = 700,
) -> BenchmarkResult:
  benchmark_cases = list(cases)
  rankings = collect_rankings_from_pipeline(
    db,
    benchmark_cases,
    algorithm=algorithm,
    k=k,
    source=source,
    scenario=scenario,
    max_to_score=max_to_score,
  )
  return evaluate_rankings(
    benchmark_cases,
    rankings,
    k=k,
    algorithm_version=ALGORITHM_VERSION,
    ranking_algorithm=algorithm,
    algorithm_config=recommendation_algorithm_metadata(ranking_algorithm=algorithm, scenario=scenario),
  )
