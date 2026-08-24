"""Pure ranking metrics used by the offline recommendation benchmark."""
from __future__ import annotations

from math import log2
from typing import Iterable


def _clean_ids(ids: Iterable[object]) -> list[str]:
  out: list[str] = []
  seen: set[str] = set()
  for raw in ids:
    value = str(raw or "").strip()
    if not value or value in seen:
      continue
    out.append(value)
    seen.add(value)
  return out


def _relevant_set(ids: Iterable[object]) -> set[str]:
  return {str(raw or "").strip() for raw in ids if str(raw or "").strip()}


def _top_k(ranked_ids: Iterable[object], k: int) -> list[str]:
  if k <= 0:
    return []
  return _clean_ids(ranked_ids)[:k]


def precision_at_k(ranked_ids: Iterable[object], relevant_ids: Iterable[object], k: int) -> float:
  top = _top_k(ranked_ids, k)
  relevant = _relevant_set(relevant_ids)
  if k <= 0 or not relevant:
    return 0.0
  hits = sum(1 for product_id in top if product_id in relevant)
  return hits / float(k)


def recall_at_k(ranked_ids: Iterable[object], relevant_ids: Iterable[object], k: int) -> float:
  top = _top_k(ranked_ids, k)
  relevant = _relevant_set(relevant_ids)
  if not relevant:
    return 0.0
  hits = sum(1 for product_id in top if product_id in relevant)
  return hits / float(len(relevant))


def hit_rate_at_k(ranked_ids: Iterable[object], relevant_ids: Iterable[object], k: int) -> float:
  top = _top_k(ranked_ids, k)
  relevant = _relevant_set(relevant_ids)
  if not top or not relevant:
    return 0.0
  return 1.0 if any(product_id in relevant for product_id in top) else 0.0


def mrr(ranked_ids: Iterable[object], relevant_ids: Iterable[object]) -> float:
  relevant = _relevant_set(relevant_ids)
  if not relevant:
    return 0.0
  for rank, product_id in enumerate(_clean_ids(ranked_ids), start=1):
    if product_id in relevant:
      return 1.0 / float(rank)
  return 0.0


def ndcg_at_k(ranked_ids: Iterable[object], relevant_ids: Iterable[object], k: int) -> float:
  top = _top_k(ranked_ids, k)
  relevant = _relevant_set(relevant_ids)
  if not top or not relevant:
    return 0.0
  dcg = sum((1.0 / log2(rank + 1)) for rank, product_id in enumerate(top, start=1) if product_id in relevant)
  ideal_hits = min(len(relevant), k)
  idcg = sum(1.0 / log2(rank + 1) for rank in range(1, ideal_hits + 1))
  return dcg / idcg if idcg > 0 else 0.0


def share_at_k(ranked_ids: Iterable[object], target_ids: Iterable[object], k: int) -> float:
  top = _top_k(ranked_ids, k)
  targets = _relevant_set(target_ids)
  if not top or not targets:
    return 0.0
  return sum(1 for product_id in top if product_id in targets) / float(len(top))


def negative_rate_at_k(ranked_ids: Iterable[object], negative_ids: Iterable[object], k: int) -> float:
  return share_at_k(ranked_ids, negative_ids, k)


def dislike_leakage_at_k(ranked_ids: Iterable[object], negative_ids: Iterable[object], k: int) -> float:
  return hit_rate_at_k(ranked_ids, negative_ids, k)


def fit_pass_rate_at_k(
  ranked_ids: Iterable[object],
  *,
  fit_positive_ids: Iterable[object],
  fit_negative_ids: Iterable[object],
  k: int,
) -> float | None:
  top = _top_k(ranked_ids, k)
  if not top:
    return 0.0
  positives = _relevant_set(fit_positive_ids)
  negatives = _relevant_set(fit_negative_ids)
  if positives:
    return sum(1 for product_id in top if product_id in positives) / float(len(top))
  if negatives:
    return sum(1 for product_id in top if product_id not in negatives) / float(len(top))
  return None


def both_at_k(
  ranked_ids: Iterable[object],
  *,
  taste_positive_ids: Iterable[object],
  fit_positive_ids: Iterable[object],
  k: int,
) -> float:
  both = _relevant_set(taste_positive_ids) & _relevant_set(fit_positive_ids)
  return share_at_k(ranked_ids, both, k)
