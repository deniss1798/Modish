from __future__ import annotations

import base64
import json
from typing import Any

import httpx

from ..config import get_openai_api_key, get_visual_analysis_text_model, get_visual_analysis_image_model


def _build_prompt(analysis: dict[str, Any]) -> str:
  palette = ", ".join([str(x) for x in (analysis.get("color_palette") or [])][:6])
  avoid = ", ".join([str(x) for x in (analysis.get("avoid_colors") or [])][:6])
  sil = ", ".join([str(x) for x in (analysis.get("recommended_silhouettes") or [])][:6])
  avoid_sil = ", ".join([str(x) for x in (analysis.get("avoid_silhouettes") or [])][:6])
  dirs = ", ".join([str(x) for x in (analysis.get("style_directions") or [])][:6])
  items = ", ".join([str(x) for x in (analysis.get("recommended_items") or [])][:8])

  return f"""Create a clean fashion infographic (no photos, no real people faces) for a user's clothing style profile.

Sections (use clear headings):
1) Colors that fit: {palette or "N/A"}
2) Colors to avoid: {avoid or "N/A"}
3) Recommended silhouettes: {sil or "N/A"}
4) Silhouettes to avoid: {avoid_sil or "N/A"}
5) Recommended items: {items or "N/A"}
6) Style directions: {dirs or "N/A"}

Style:
- modern minimal, light background
- 1:1 square layout, readable typography
- use small color swatches for palette
- do not include any sensitive attributes or judgments
"""


async def generate_style_visual(analysis: dict[str, Any]) -> str:
  """
  Generates infographic image and returns a data URL: data:image/png;base64,...
  """
  api_key = get_openai_api_key()
  text_model = get_visual_analysis_text_model()
  image_model = get_visual_analysis_image_model()
  prompt = _build_prompt(analysis)

  payload: dict[str, Any] = {
    "model": text_model,
    "input": prompt,
    "tools": [
      {
        "type": "image_generation",
        "model": image_model,
        "size": "1024x1024",
        "background": "opaque",
      }
    ],
  }
  headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

  async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=15.0)) as client:
    r = await client.post(
      "https://api.openai.com/v1/responses",
      headers=headers,
      content=json.dumps(payload),
    )
  if r.status_code >= 400:
    raise RuntimeError(f"Image generation error: HTTP {r.status_code}: {r.text[:800]}")

  data = r.json()
  output = data.get("output") or []
  b64: str | None = None
  if isinstance(output, list):
    for item in output:
      if isinstance(item, dict) and item.get("type") == "image_generation_call":
        b64 = item.get("result")
        break
  if not isinstance(b64, str) or not b64.strip():
    raise RuntimeError("Image generation returned no image data")

  # Validate base64 quickly
  base64.b64decode(b64, validate=True)
  return f"data:image/png;base64,{b64}"

