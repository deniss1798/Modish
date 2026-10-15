"""Serialize and record one accepted feed delivery; caller owns commit."""
from typing import Any

from ..schemas.api_product import product_to_api
from .recommendation_config import recommendation_algorithm_metadata


def serialize_feed_delivery(db, *, user_id, scored, source=None, scenario="daily"):
  from .product_analytics_service import log_feed_impressions

  ranking_metadata: dict[str, dict[str, Any]] = {}
  for rank, s in enumerate(scored, start=1):
    ranking_metadata[s.product.id] = {
      **recommendation_algorithm_metadata(ranking_algorithm=s.ranking_algorithm, scenario=scenario),
      "algorithm_version": s.algorithm_version,
      "rank": rank,
      "candidate_source": ",".join(s.candidate_sources),
      "candidate_sources": list(s.candidate_sources),
      "fit_score": s.breakdown.get("fit_score"),
      "taste_score": s.breakdown.get("taste_score"),
      "context_score": s.breakdown.get("context_score"),
      "quality_score": s.breakdown.get("quality_score"),
      "exploration_score": s.breakdown.get("exploration_score"),
      "final_score": s.final_score,
    }
  log_feed_impressions(
    db,
    user_id=user_id,
    product_ids=[s.product.id for s in scored],
    source=source,
    ranking_metadata=ranking_metadata,
  )
  out: list[dict[str, Any]] = []
  for s in scored:
    out.append(
      {
        "product": product_to_api(s.product),
        "final_score": s.final_score,
        "breakdown": s.breakdown,
        "candidate_sources": s.candidate_sources,
        "algorithm_version": s.algorithm_version,
        "ranking_algorithm": s.ranking_algorithm,
        "reason": s.reason,
        "reasons": s.reasons,
      }
    )
  return out
