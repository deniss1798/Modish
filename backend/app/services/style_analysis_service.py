from __future__ import annotations

import base64
import json
from typing import Any

import httpx

from ..config import get_openai_api_key, get_style_analysis_model


STYLE_ANALYSIS_PROMPT = """Analyze this photo strictly for clothing recommendations.

Do NOT evaluate:
- attractiveness
- face details
- beauty
- age
- ethnicity

Extract only:
- suitable colors
- colors to avoid
- contrast level
- visible body proportions relevant to clothing
- recommended silhouettes
- silhouettes to avoid
- recommended clothing items
- style directions

Return JSON only.
"""


_RESULT_SCHEMA: dict[str, Any] = {
  "type": "object",
  "additionalProperties": False,
  "properties": {
    "color_palette": {"type": "array", "items": {"type": "string"}},
    "avoid_colors": {"type": "array", "items": {"type": "string"}},
    "contrast_level": {"type": "string"},
    "body_proportions": {"type": "string"},
    "recommended_silhouettes": {"type": "array", "items": {"type": "string"}},
    "avoid_silhouettes": {"type": "array", "items": {"type": "string"}},
    "recommended_items": {"type": "array", "items": {"type": "string"}},
    "avoid_items": {"type": "array", "items": {"type": "string"}},
    "style_directions": {"type": "array", "items": {"type": "string"}},
    "summary": {"type": "string"},
  },
  "required": [
    "color_palette",
    "avoid_colors",
    "contrast_level",
    "body_proportions",
    "recommended_silhouettes",
    "avoid_silhouettes",
    "recommended_items",
    "avoid_items",
    "style_directions",
    "summary",
  ],
}


def _data_url(content_type: str, raw: bytes) -> str:
  b64 = base64.b64encode(raw).decode("ascii")
  return f"data:{content_type};base64,{b64}"


async def analyze_photo_bytes(*, content_type: str, raw: bytes) -> dict[str, Any]:
  """
  Vision-анализ фото через OpenAI Responses API.

  Возвращает СТРОГО один JSON-объект формата:
  {
    "color_palette": [],
    "avoid_colors": [],
    "contrast_level": "",
    "body_proportions": "",
    "recommended_silhouettes": [],
    "avoid_silhouettes": [],
    "recommended_items": [],
    "avoid_items": [],
    "style_directions": [],
    "summary": ""
  }
  """
  api_key = get_openai_api_key()
  model = get_style_analysis_model()

  ct = (content_type or "image/jpeg").lower()
  if not raw:
    raise RuntimeError("Empty image bytes")
  image_url = _data_url(ct, raw)

  payload: dict[str, Any] = {
    "model": model,
    "input": [
      {
        "role": "user",
        "content": [
          {"type": "input_text", "text": STYLE_ANALYSIS_PROMPT},
          {"type": "input_image", "image_url": image_url},
        ],
      }
    ],
    "text": {
      "format": {
        "type": "json_schema",
        "name": "style_analysis_result",
        "schema": _RESULT_SCHEMA,
        "strict": True,
      }
    },
  }

  headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

  async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0)) as client:
    r = await client.post(
      "https://api.openai.com/v1/responses",
      headers=headers,
      content=json.dumps(payload),
    )
  if r.status_code >= 400:
    # Возвращаем максимально полезную ошибку, но без утечки ключа.
    raise RuntimeError(f"Vision API error: HTTP {r.status_code}: {r.text[:800]}")

  data = r.json()
  # В большинстве примеров есть output_text; при json_schema это будет JSON-строка.
  text = data.get("output_text")
  if not isinstance(text, str) or not text.strip():
    raise RuntimeError("Vision API returned empty output_text")

  try:
    result = json.loads(text)
  except json.JSONDecodeError as exc:
    raise RuntimeError(f"Vision API returned non-JSON output: {text[:400]}") from exc

  # Дополнительная валидация на уровне Python (на случай несовпадений).
  if not isinstance(result, dict):
    raise RuntimeError("Vision API returned invalid JSON root (expected object)")
  for k in _RESULT_SCHEMA["required"]:
    if k not in result:
      raise RuntimeError(f"Vision API JSON missing required key: {k}")
  return result

