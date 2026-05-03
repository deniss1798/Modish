"""Структурированный результат фото-анализа (ТЗ §15.1), версия схемы v1."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class PhotoAnalysisV1(BaseModel):
  """Поля, которые ожидает клиент после AI; мок заполняет те же ключи."""

  body_type: str = Field(description="rectangle|triangle|...")
  color_temperature: str = Field(description="cool|warm|neutral")
  contrast_level: str = Field(description="low|medium|high")
  face_shape: str = Field(description="oval|round|...")
  recommended_colors: list[str] = Field(default_factory=list)
  avoid_colors: list[str] = Field(default_factory=list)
  recommended_fits: list[str] = Field(default_factory=list)
  avoid_fits: list[str] = Field(default_factory=list)
  style_summary: str = ""
  confidence_score: float = Field(default=0.74, ge=0.0, le=1.0)


def mock_photo_analysis_v1() -> PhotoAnalysisV1:
  return PhotoAnalysisV1(
    body_type="rectangle",
    color_temperature="cool",
    contrast_level="medium",
    face_shape="oval",
    recommended_colors=["navy", "gray", "white", "burgundy"],
    avoid_colors=["neon_yellow", "warm_orange"],
    recommended_fits=["straight", "structured", "minimal"],
    avoid_fits=["too_baggy", "shapeless"],
    style_summary=(
      "Пользователю подойдут спокойные чистые сочетания, прямые силуэты и минималистичные образы."
    ),
    confidence_score=0.74,
  )


def build_profile_json_after_analysis(analysis: PhotoAnalysisV1, *, source: str) -> dict[str, Any]:
  """Единый формат profile_json после анализа фото."""
  now = datetime.now(timezone.utc).isoformat()
  return {
    "version": 1,
    "source": source,
    "analyzed_at": now,
    "analysis": analysis.model_dump(),
  }


def extract_analysis_section(profile_json: dict[str, Any] | None) -> dict[str, Any]:
  """Читает блок analysis из profile_json или старый плоский формат."""
  pj = dict(profile_json or {})
  nested = pj.get("analysis")
  if isinstance(nested, dict) and nested:
    return nested
  return pj
