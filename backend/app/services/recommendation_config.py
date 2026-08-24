"""Configuration for product feedback semantics.

Phase 1 of MIE keeps the existing API, but gives every event a clear
meaning and configurable weight.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any


ALGORITHM_VERSION = "mie_phase1_feedback"

EVENT_ALIASES = {
  "buy_click": "affiliate_click",
}

EVENT_WEIGHTS: dict[str, float] = {
  "impression": 0.0,
  "view": 0.25,
  "skip": -0.5,
  "dislike": -3.0,
  "like": 2.0,
  "save": 4.0,
  "unsave": -2.0,
  "open_product": 1.0,
  "affiliate_click": 5.0,
  "purchase": 10.0,
  "post_purchase_positive": 15.0,
  "post_purchase_negative": -10.0,
}

VALID_EVENT_TYPES = frozenset(EVENT_WEIGHTS)

VALID_DISLIKE_REASONS = frozenset(
  {
    "not_my_style",
    "dont_like_color",
    "dont_like_fit",
    "dont_like_category",
    "dont_like_brand",
    "too_expensive",
    "already_have_similar",
    "dont_like_design",
    "other",
  }
)

LAST_SEEN_EVENTS = frozenset(
  {
    "impression",
    "view",
    "open_product",
    "affiliate_click",
  }
)

POSITIVE_LIST_EVENTS = frozenset(
  {
    "like",
    "save",
    "affiliate_click",
    "purchase",
    "post_purchase_positive",
  }
)

NEGATIVE_LIST_EVENTS = frozenset()

TASTE_UPDATE_EVENTS = frozenset(
  {
    "view",
    "skip",
    "dislike",
    "like",
    "save",
    "unsave",
    "open_product",
    "affiliate_click",
    "purchase",
    "post_purchase_positive",
    "post_purchase_negative",
  }
)

HIDE_FOR: dict[str, timedelta] = {
  "skip": timedelta(days=1),
  "dislike": timedelta(days=3650),
  "like": timedelta(hours=6),
  "save": timedelta(days=3650),
  "unsave": timedelta(hours=6),
  "affiliate_click": timedelta(days=3650),
  "purchase": timedelta(days=3650),
  "post_purchase_positive": timedelta(days=3650),
  "post_purchase_negative": timedelta(days=3650),
}


def normalize_event_type(raw: str) -> str:
  value = (raw or "").strip().lower()
  value = EVENT_ALIASES.get(value, value)
  if value not in VALID_EVENT_TYPES:
    allowed = ", ".join(sorted(VALID_EVENT_TYPES | frozenset(EVENT_ALIASES)))
    raise ValueError(f"Unsupported event_type: {raw!r}. Allowed: {allowed}")
  return value


def event_weight(event_type: str) -> float:
  return float(EVENT_WEIGHTS[normalize_event_type(event_type)])


def dislike_reason_from_meta(meta: dict[str, Any] | None) -> str | None:
  if not isinstance(meta, dict):
    return None
  reason = str(meta.get("reason") or "").strip()
  if not reason:
    return None
  if reason not in VALID_DISLIKE_REASONS:
    raise ValueError(f"Unsupported dislike reason: {reason!r}")
  return reason


def normalized_event_meta(
  *,
  event_type: str,
  meta: dict[str, Any] | None,
  original_event_type: str | None = None,
) -> dict[str, Any]:
  out = dict(meta or {})
  if original_event_type and original_event_type != event_type:
    out["legacy_event_type"] = original_event_type
  if event_type in ("dislike", "post_purchase_negative"):
    reason = dislike_reason_from_meta(out)
    if reason:
      out["reason"] = reason
  return out


def feature_targets_for_event(event_type: str, meta: dict[str, Any]) -> set[str]:
  """Which product features should this feedback update in legacy taste weights."""
  if event_type == "impression":
    return set()
  if event_type in POSITIVE_LIST_EVENTS or event_type in {
    "view",
    "open_product",
    "unsave",
  }:
    return {"category", "brand", "color", "style"}
  if event_type == "skip":
    return {"category", "style"}
  if event_type in ("dislike", "post_purchase_negative"):
    reason = dislike_reason_from_meta(meta)
    if reason == "dont_like_color":
      return {"color"}
    if reason == "dont_like_category":
      return {"category"}
    if reason == "dont_like_brand":
      return {"brand"}
    if reason in ("not_my_style", "dont_like_design"):
      return {"style", "category"}
    if reason in ("dont_like_fit", "too_expensive", "already_have_similar"):
      return set()
    return {"category", "brand", "color", "style"}
  return set()
