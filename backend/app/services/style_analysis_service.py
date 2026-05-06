from __future__ import annotations

import base64
import json
import asyncio
from typing import Any

import httpx

from ..config import (
  get_ai_api_key,
  get_ai_base_url,
  get_ai_chat_completions_path,
  get_ai_mode,
  get_ai_responses_path,
  get_style_analysis_model,
)


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
  api_key = get_ai_api_key()
  model = get_style_analysis_model()
  base_url = get_ai_base_url().rstrip("/")
  mode = get_ai_mode()

  ct = (content_type or "image/jpeg").lower()
  if not raw:
    raise RuntimeError("Empty image bytes")
  image_url = _data_url(ct, raw)

  headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

  async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0)) as client:
    if mode == "responses":
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
      url = f"{base_url}{get_ai_responses_path()}"
      request_body = json.dumps(payload)
    elif mode == "chat":
      # OpenAI-compatible Chat Completions (часто поддерживается провайдерами вроде DeepSeek).
      # ВАЖНО: многие провайдеры не поддерживают multimodal schema вида
      # content: [{type:"text"...},{type:"image_url"...}] и ожидают строку.
      # Поэтому для совместимости отправляем data URL изображения как часть текста.
      payload = {
        "model": model,
        "messages": [
          {
            "role": "user",
            "content": f"{STYLE_ANALYSIS_PROMPT}\n\nIMAGE_DATA_URL:\n{image_url}\n",
          }
        ],
        "temperature": 0,
      }
      url = f"{base_url}{get_ai_chat_completions_path()}"
      request_body = json.dumps(payload)
    else:
      raise RuntimeError(f"Unsupported AI_MODE: {mode!r} (expected 'responses' or 'chat')")

    # OpenAI/провайдеры иногда отдают transient 5xx. Ретраим коротко, чтобы UX не ломался.
    r: httpx.Response | None = None
    last_exc: Exception | None = None
    for attempt in range(1, 4):
      try:
        r = await client.post(url, headers=headers, content=request_body)
        if r.status_code >= 500:
          await asyncio.sleep(0.6 * attempt)
          continue
        break
      except (httpx.TimeoutException, httpx.NetworkError) as exc:
        last_exc = exc
        await asyncio.sleep(0.6 * attempt)
        continue
    if r is None:
      raise RuntimeError(f"Vision API request failed (network/timeout): {last_exc!r}")
  if r.status_code >= 400:
    # Возвращаем максимально полезную ошибку, но без утечки ключа.
    raise RuntimeError(f"Vision API error: HTTP {r.status_code}: {r.text[:800]}")

  data = r.json()
  if mode == "responses":
    # В большинстве примеров есть output_text; при json_schema это будет JSON-строка.
    text = data.get("output_text")
    if not isinstance(text, str) or not text.strip():
      raise RuntimeError("Vision API returned empty output_text")
  else:
    try:
      text = data["choices"][0]["message"]["content"]
    except Exception as exc:  # noqa: BLE001
      raise RuntimeError(f"Chat Completions returned unexpected shape: {str(data)[:800]}") from exc
    if not isinstance(text, str) or not text.strip():
      raise RuntimeError("Chat Completions returned empty message.content")

  try:
    result = json.loads(text)
  except json.JSONDecodeError as exc:
    raise RuntimeError(f"Vision API returned non-JSON output: {text[:400]}") from exc

  # Дополнительная валидация на уровне Python (на случай несовпадений).
  if not isinstance(result, dict):
    raise RuntimeError("Vision API returned invalid JSON root (expected object)")

  # Часто провайдеры возвращают обёртку вида {"result": {...}} или {"data": {...}}.
  if len(result) == 1:
    only_val = next(iter(result.values()))
    if isinstance(only_val, dict):
      result = only_val

  required = list(_RESULT_SCHEMA["required"])
  props: dict[str, Any] = _RESULT_SCHEMA.get("properties") or {}

  # Для нестабильных провайдеров (DeepSeek chat) — мягко дозаполняем пропуски,
  # чтобы клиент не падал.
  for k in required:
    if k in result:
      continue
    spec = props.get(k) if isinstance(props, dict) else None
    if isinstance(spec, dict) and spec.get("type") == "array":
      result[k] = []
    else:
      result[k] = ""

  # Базовая проверка типов после нормализации.
  if not isinstance(result.get("color_palette"), list):
    result["color_palette"] = []
  if not isinstance(result.get("avoid_colors"), list):
    result["avoid_colors"] = []
  if not isinstance(result.get("recommended_silhouettes"), list):
    result["recommended_silhouettes"] = []
  if not isinstance(result.get("avoid_silhouettes"), list):
    result["avoid_silhouettes"] = []
  if not isinstance(result.get("recommended_items"), list):
    result["recommended_items"] = []
  if not isinstance(result.get("avoid_items"), list):
    result["avoid_items"] = []
  if not isinstance(result.get("style_directions"), list):
    result["style_directions"] = []
  for k in ("contrast_level", "body_proportions", "summary"):
    if not isinstance(result.get(k), str):
      result[k] = ""

  return result

