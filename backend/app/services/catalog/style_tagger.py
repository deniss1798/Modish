"""Правило-based определение style_tags товара по названию/описанию/категории.

По спецификации MIE (гл. 31.11) style-теги в MVP проставляются правилами,
без ML. Теги согласованы со словарём онбординга (style_preferences)
и со сценариями FitProfile.style_scenarios, чтобы Style Match в
recommendation_engine работал в обе стороны:

  style_preferences: casual | minimalism | smart_casual | elegant |
                     streetwear | romantic | business | sporty
  style_scenarios:   daily | office | evening | minimal | casual
"""
from __future__ import annotations

import re

# Максимум тегов на товар
_MAX_TAGS = 6

# (стем в тексте, теги)
_KEYWORD_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
  # спорт
  ("спорт", ("sporty", "daily")),
  ("трениров", ("sporty", "daily")),
  ("фитнес", ("sporty", "daily")),
  ("для_бега", ("sporty", "daily")),
  ("джогер", ("sporty", "streetwear", "daily")),
  # деловой / офис
  ("классическ", ("business", "office", "smart_casual")),
  ("офис", ("business", "office", "smart_casual")),
  ("делов", ("business", "office")),
  ("костюмн", ("business", "office", "smart_casual")),
  ("пиджак", ("business", "office", "smart_casual")),
  ("жакет", ("business", "office", "smart_casual")),
  ("со_стрелками", ("business", "office")),
  ("оксфорд", ("smart_casual", "office")),
  ("рубашка", ("smart_casual", "office", "daily")),
  ("сорочка", ("smart_casual", "office")),
  ("блуза", ("smart_casual", "office", "daily")),
  ("блузка", ("smart_casual", "office", "daily")),
  # вечерний / элегантный
  ("вечерн", ("elegant", "evening")),
  ("элегант", ("elegant", "evening", "smart_casual")),
  ("коктейль", ("elegant", "evening")),
  ("атлас", ("elegant", "evening")),
  ("шелк", ("elegant", "evening")),
  ("шёлк", ("elegant", "evening")),
  ("сатин", ("elegant", "evening")),
  ("пайетк", ("elegant", "evening")),
  ("бархат", ("elegant", "evening")),
  # романтичный
  ("романтич", ("romantic", "evening")),
  ("кружев", ("romantic", "elegant", "evening")),
  ("цветочн", ("romantic", "daily")),
  ("рюш", ("romantic",)),
  ("волан", ("romantic",)),
  ("бант", ("romantic",)),
  # стритвир
  ("оверсайз", ("streetwear", "casual", "daily")),
  ("oversize", ("streetwear", "casual", "daily")),
  ("oversized", ("streetwear", "casual", "daily")),
  ("стрит", ("streetwear", "casual", "daily")),
  ("карго", ("streetwear", "casual", "daily")),
  ("граффити", ("streetwear", "casual")),
  ("скейт", ("streetwear", "casual")),
  ("унисекс", ("streetwear", "casual")),
  ("худи", ("streetwear", "casual", "daily")),
  ("свитшот", ("streetwear", "casual", "daily")),
  ("бомбер", ("streetwear", "casual", "daily")),
  ("с_принтом", ("streetwear", "casual", "daily")),
  ("принт", ("casual", "daily")),
  # минимализм / база
  ("базов", ("minimalism", "minimal", "casual", "daily")),
  ("минимал", ("minimalism", "minimal", "daily")),
  ("однотон", ("minimalism", "minimal", "daily")),
  ("лаконичн", ("minimalism", "minimal", "daily")),
  ("прямого_кроя", ("minimalism", "minimal", "smart_casual")),
  # повседневный
  ("повседневн", ("casual", "daily")),
  ("casual", ("casual", "daily")),
  ("джинс", ("casual", "daily")),
  ("деним", ("casual", "daily")),
  ("комфорт", ("casual", "daily")),
  ("уличн", ("streetwear", "casual", "daily")),
)

# Дефолтные теги по канонической категории (когда по тексту ничего не нашли
# или чтобы дополнить контекст)
_CATEGORY_DEFAULTS: dict[str, tuple[str, ...]] = {
  "футболки": ("casual", "daily"),
  "джинсы": ("casual", "daily"),
  "худи": ("streetwear", "casual", "daily"),
  "шорты": ("casual", "daily"),
  "спорт": ("sporty", "daily"),
  "рубашки": ("smart_casual", "office", "daily"),
  "брюки": ("smart_casual", "office", "daily"),
  "джемперы": ("casual", "smart_casual", "daily"),
  "платья": ("romantic", "daily"),
  "юбки": ("romantic", "daily"),
  "верхний_слой": ("casual", "daily"),
  "обувь": ("casual", "daily"),
  "сумки": ("casual", "daily"),
  "аксессуары": ("casual", "daily"),
  "костюмы": ("business", "office", "smart_casual"),
  "комбинезоны": ("casual", "daily"),
}

_WS_RE = re.compile(r"[\s\-–—/]+")


def infer_style_tags(
  *,
  title: str | None = None,
  description: str | None = None,
  category: str | None = None,
  subcategory: str | None = None,
) -> list[str]:
  """Определяет style_tags товара. Всегда возвращает непустой список
  для товаров одежды (минимум casual/daily)."""
  hay_parts = [
    str(title or ""),
    str(subcategory or ""),
    # описание длинное — берём начало, чтобы не ловить теги из «сочетайте с…»
    str(description or "")[:400],
  ]
  hay = _WS_RE.sub("_", " ".join(hay_parts).strip().lower())

  tags: list[str] = []

  def add(ts: tuple[str, ...]) -> None:
    for t in ts:
      if t not in tags:
        tags.append(t)

  for stem, ts in _KEYWORD_RULES:
    if len(tags) >= _MAX_TAGS:
      break
    if stem in hay:
      add(ts)

  cat = str(category or "").strip().lower()
  if cat in _CATEGORY_DEFAULTS:
    add(_CATEGORY_DEFAULTS[cat])

  if not tags:
    tags = ["casual", "daily"]
  return tags[:_MAX_TAGS]
