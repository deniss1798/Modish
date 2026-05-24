"""Онбординг v2: анализ фото → тип фигуры, цветотип (structured output)."""

from __future__ import annotations

import re
from typing import Any

from .style_analysis_service import analyze_photo_bytes as _vision_analyze

# Варианты для экрана подтверждения / ручной правки
BODY_SHAPE_OPTIONS: tuple[str, ...] = (
  "Прямоугольник",
  "Груша",
  "Яблоко",
  "Песочные часы",
  "Перевёрнутый треугольник",
  "Овал",
)

COLOR_TYPE_OPTIONS: tuple[str, ...] = (
  "Холодная зима",
  "Холодное лето",
  "Тёплая весна",
  "Тёплая осень",
  "Нейтральный",
)

HEIGHT_CATEGORY_OPTIONS: tuple[str, ...] = (
  "Низкий",
  "Средний",
  "Высокий",
)


def _pick_body_shape(body_proportions: str, silhouettes: list[str]) -> str:
  text = f"{body_proportions} {' '.join(silhouettes)}".lower()
  rules: list[tuple[str, str]] = [
    ("песочн", "Песочные часы"),
    ("hourglass", "Песочные часы"),
    ("груш", "Груша"),
    ("pear", "Груша"),
    ("яблок", "Яблоко"),
    ("apple", "Яблоко"),
    ("прямоуголь", "Прямоугольник"),
    ("rectangle", "Прямоугольник"),
    ("перевёрнут", "Перевёрнутый треугольник"),
    ("inverted", "Перевёрнутый треугольник"),
    ("овал", "Овал"),
  ]
  for needle, label in rules:
    if needle in text:
      return label
  if silhouettes:
    return silhouettes[0][:64]
  return "Прямоугольник"


def _pick_color_type(palette: list[str], contrast: str, summary: str) -> str:
  joined = " ".join(palette + [contrast, summary]).lower()
  if any(x in joined for x in ("зим", "winter", "холодн", "cool", "контраст")):
    if "лет" in joined or "summer" in joined:
      return "Холодное лето"
    return "Холодная зима"
  if any(x in joined for x in ("весн", "spring", "тёпл", "тепл", "warm")):
    return "Тёплая весна"
  if any(x in joined for x in ("осен", "autumn", "fall")):
    return "Тёплая осень"
  if palette:
    return "Нейтральный"
  return "Нейтральный"


def _pick_height_category(body_proportions: str) -> str | None:
  m = re.search(r"(\d{3})\s*см", body_proportions or "", re.I)
  if m:
    h = int(m.group(1))
    if h < 162:
      return "Низкий"
    if h > 178:
      return "Высокий"
    return "Средний"
  low = (body_proportions or "").lower()
  if "низк" in low or "petite" in low:
    return "Низкий"
  if "высок" in low or "tall" in low:
    return "Высокий"
  return None


async def analyze_onboarding_photo(*, content_type: str, raw: bytes) -> dict[str, Any]:
  """
  Вызывает vision API и возвращает:
  - photo_analysis (raw)
  - body_shape, color_type, height_category для экрана подтверждения
  """
  raw_analysis = await _vision_analyze(content_type=content_type, raw=raw)
  palette = [str(x) for x in (raw_analysis.get("color_palette") or []) if str(x).strip()]
  silhouettes = [
    str(x) for x in (raw_analysis.get("recommended_silhouettes") or []) if str(x).strip()
  ]
  body_proportions = str(raw_analysis.get("body_proportions") or "")
  contrast = str(raw_analysis.get("contrast_level") or "")
  summary = str(raw_analysis.get("summary") or "")

  body_shape = _pick_body_shape(body_proportions, silhouettes)
  color_type = _pick_color_type(palette, contrast, summary)
  height_category = _pick_height_category(body_proportions)

  return {
    "photo_analysis": raw_analysis,
    "body_shape": body_shape,
    "color_type": color_type,
    "height_category": height_category,
    "summary": summary,
    "options": {
      "body_shapes": list(BODY_SHAPE_OPTIONS),
      "color_types": list(COLOR_TYPE_OPTIONS),
      "height_categories": list(HEIGHT_CATEGORY_OPTIONS),
    },
  }
