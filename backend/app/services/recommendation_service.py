from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from ..models import Recommendation, StyleProfile
from ..schemas.photo_analysis import extract_analysis_section


@dataclass(frozen=True)
class RecommendationItem:
  name: str
  type: str
  color: str
  reason: str
  # future_fields: price, url, image_url, brand

  def to_json(self) -> dict[str, Any]:
    return {
      "name": self.name,
      "type": self.type,
      "color": self.color,
      "reason": self.reason,
      "price": None,
      "url": None,
      "image_url": None,
      "brand": None,
    }


def _pick_palette(a: dict[str, Any]) -> list[str]:
  raw = a.get("color_palette") or []
  out: list[str] = []
  for x in raw:
    s = str(x).strip().lower()
    if s and s not in out:
      out.append(s)
  return out[:5]


def _pick_silhouettes(a: dict[str, Any]) -> list[str]:
  raw = a.get("recommended_silhouettes") or []
  out: list[str] = []
  for x in raw:
    s = str(x).strip().lower()
    if s and s not in out:
      out.append(s)
  return out[:5]


def _pick_styles(a: dict[str, Any]) -> list[str]:
  raw = a.get("style_directions") or []
  out: list[str] = []
  for x in raw:
    s = str(x).strip().lower()
    if s and s not in out:
      out.append(s)
  return out[:5]


def _scenario_ru(scenario: str) -> str:
  s = (scenario or "").strip().lower()
  return {"daily": "каждый день", "office": "офис", "evening": "вечер"}.get(s, "каждый день")


def _base_outfit_template(scenario: str) -> dict[str, str]:
  s = (scenario or "").strip().lower()
  if s == "office":
    return {"top": "рубашка", "bottom": "чиносы", "layer": "пиджак", "shoes": "лоферы"}
  if s == "evening":
    return {"top": "водолазка", "bottom": "брюки", "layer": "пальто", "shoes": "ботинки"}
  return {"top": "лонгслив", "bottom": "прямые джинсы", "layer": "жакет", "shoes": "кроссовки"}


def _item_reason(style: str | None, silhouette: str | None) -> str:
  parts: list[str] = []
  if silhouette:
    parts.append(f"силуэт: {silhouette}")
  if style:
    parts.append(f"направление: {style}")
  return " · ".join(parts) if parts else "соответствует вашему профилю"


def generate_recommendations(
  db: Session,
  profile: StyleProfile,
  *,
  count: int,
  scenario: str,
) -> list[Recommendation]:
  """
  Генерация рекомендаций, зависящая от style_profile.analysis:
  - color_palette
  - recommended_silhouettes
  - style_directions

  Возвращает созданные Recommendation (db.add + db.flush, без commit).
  """
  a = extract_analysis_section(profile.profile_json or {})
  palette = _pick_palette(a) or ["navy", "white", "graphite"]
  silhouettes = _pick_silhouettes(a) or ["straight", "structured"]
  styles = _pick_styles(a) or ["minimal"]

  base = _base_outfit_template(scenario)
  now = datetime.now(timezone.utc)
  created: list[Recommendation] = []

  count = max(1, min(10, int(count)))
  for i in range(count):
    c1 = palette[i % len(palette)]
    c2 = palette[(i + 1) % len(palette)]
    sil = silhouettes[i % len(silhouettes)]
    style = styles[i % len(styles)]

    # Сохраняем старый формат items (map) для Flutter v1
    items_map = {
      "top": f"{c1} {base['top']}",
      "bottom": f"{c2} {base['bottom']}",
      "layer": f"{palette[0]} {base['layer']}",
      "shoes": base["shoes"],
    }

    items_v2 = [
      RecommendationItem(name=items_map["top"], type="top", color=c1, reason=_item_reason(style, sil)),
      RecommendationItem(name=items_map["bottom"], type="bottom", color=c2, reason=_item_reason(style, sil)),
      RecommendationItem(name=items_map["layer"], type="layer", color=palette[0], reason=_item_reason(style, sil)),
      RecommendationItem(name=items_map["shoes"], type="shoes", color="", reason=_item_reason(style, sil)),
    ]

    title = f"Образ под ваш стиль · {_scenario_ru(scenario)}"
    description = f"Палитра: {', '.join(palette[:3])}. Силуэт: {sil}. Направление: {style}."

    content = {
      "items": items_map,  # legacy
      "items_v2": [it.to_json() for it in items_v2],  # new model
      "why_it_fits": [
        f"собрано из вашей палитры ({', '.join(palette[:3])})",
        f"учтён силуэт ({sil})",
        f"поддерживает направление стиля ({style})",
      ],
      "alternatives": [
        "верх можно заменить на однотонную футболку из той же палитры",
        "обувь можно заменить на более формальную пару без смены силуэта",
      ],
    }

    tags = {
      "styles": [style],
      "colors": palette[:4],
      "silhouettes": [sil],
      "occasion": [(scenario or "daily").strip().lower() or "daily"],
      "item_types": ["top", "bottom", "layer", "shoes"],
    }

    rec = Recommendation(
      id=str(uuid4()),
      user_id=profile.user_id,
      type="outfit",
      title=title,
      description=description,
      content_json=content,
      tags_json=tags,
      status="active",
      created_at=now,
      updated_at=now,
    )
    db.add(rec)
    created.append(rec)

  db.flush()
  return created

