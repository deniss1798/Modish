"""Структурированный результат фото-анализа (ОДЕЖДА), версия схемы v2.

v2 — строгий формат результата анализа для рекомендаций одежды.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class PhotoAnalysisV2(BaseModel):
  """Строгий формат результата анализа фото (по ТЗ)."""

  color_palette: list[str] = Field(default_factory=list)
  avoid_colors: list[str] = Field(default_factory=list)
  contrast_level: str = ""
  body_proportions: str = ""
  recommended_silhouettes: list[str] = Field(default_factory=list)
  avoid_silhouettes: list[str] = Field(default_factory=list)
  recommended_items: list[str] = Field(default_factory=list)
  avoid_items: list[str] = Field(default_factory=list)
  style_directions: list[str] = Field(default_factory=list)
  summary: str = ""


def build_profile_json_after_analysis(analysis: dict[str, Any] | PhotoAnalysisV2, *, source: str) -> dict[str, Any]:
  """Единый формат profile_json после анализа фото."""
  now = datetime.now(timezone.utc).isoformat()
  if isinstance(analysis, PhotoAnalysisV2):
    analysis_json = analysis.model_dump()
  else:
    analysis_json = dict(analysis)
  return {
    "version": 1,
    "source": source,
    "analyzed_at": now,
    "analysis": analysis_json,
  }


def extract_analysis_section(profile_json: dict[str, Any] | None) -> dict[str, Any]:
  """Читает блок analysis из profile_json или старый плоский формат."""
  pj = dict(profile_json or {})
  nested = pj.get("analysis")
  if isinstance(nested, dict) and nested:
    return nested
  return pj
