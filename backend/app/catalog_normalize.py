from __future__ import annotations

import re
from typing import Any

CANON_COLORS = frozenset(
  {
    "black",
    "white",
    "cream",
    "navy",
    "grey",
    "blue",
    "brown",
    "green",
    "burgundy",
  }
)

_COLOR_ALIASES: dict[str, str] = {
  "gray": "grey",
  "graphite": "grey",
  "charcoal": "grey",
  "offwhite": "cream",
  "ivory": "cream",
  "beige": "cream",
  "sand": "cream",
  "indigo": "blue",
  "denim": "blue",
  "olive": "green",
  "wine": "burgundy",
  "maroon": "burgundy",
}

# Canonical categories align with outfit_service candidate() lookups (Russian + English fallbacks).
_CATEGORY_ALIASES: dict[str, str] = {
  "tshirt": "футболки",
  "t-shirt": "футболки",
  "t_shirt": "футболки",
  "tee": "футболки",
  "tees": "футболки",
  "top": "футболки",
  "tops": "футболки",
  "tshirts": "футболки",
  "футболка": "футболки",
  "футболки": "футболки",
  "shirt": "рубашки",
  "shirts": "рубашки",
  "рубашка": "рубашки",
  "рубашки": "рубашки",
  "blouse": "рубашки",
  "jean": "джинсы",
  "jeans": "джинсы",
  "denim": "джинсы",
  "джинсы": "джинсы",
  "trouser": "брюки",
  "trousers": "брюки",
  "pants": "брюки",
  "chinos": "брюки",
  "брюки": "брюки",
  "outerwear": "верхний_слой",
  "outer": "верхний_слой",
  "jacket": "верхний_слой",
  "coat": "верхний_слой",
  "layer": "верхний_слой",
  "верхний_слой": "верхний_слой",
  "верхнийслой": "верхний_слой",
  "пальто": "верхний_слой",
  "куртка": "верхний_слой",
  "куртки": "верхний_слой",
  "жакет": "верхний_слой",
  "пиджак": "верхний_слой",
  "shoe": "обувь",
  "shoes": "обувь",
  "sneakers": "обувь",
  "boots": "обувь",
  "sneaker": "обувь",
  "boot": "обувь",
  "обувь": "обувь",
  "bag": "сумки",
  "bags": "сумки",
  "сумка": "сумки",
  "сумки": "сумки",
  "accessory": "аксессуары",
  "accessories": "аксессуары",
  "аксессуар": "аксессуары",
  "аксессуары": "аксессуары",
}

_CANONICAL_CATEGORIES = frozenset(_CATEGORY_ALIASES.values())


def normalize_category(raw: str | None) -> str:
  if raw is None:
    return ""
  s = str(raw).strip().lower().replace(" ", "_").replace("-", "_")
  if not s:
    return ""
  if s in _CATEGORY_ALIASES:
    return _CATEGORY_ALIASES[s]
  if s in _CANONICAL_CATEGORIES:
    return s
  return s


def normalize_product_colors(raw: list[str] | None) -> list[str]:
  out: list[str] = []
  for x in raw or []:
    s = str(x).strip().lower().replace(" ", "_")
    if not s:
      continue
    s = _COLOR_ALIASES.get(s, s)
    if s in CANON_COLORS and s not in out:
      out.append(s)
  return out


def normalize_colors_value(raw: Any) -> list[str]:
  if raw is None:
    return []
  if isinstance(raw, str):
    items = _split_listish(raw)
  elif isinstance(raw, list):
    items = raw
  else:
    items = [str(raw)]
  return normalize_product_colors([str(x) for x in items if str(x).strip()])


_SIZE_SPLIT_RE = re.compile(r"[,;|/]+")


def _split_listish(s: str) -> list[str]:
  parts = [p.strip() for p in _SIZE_SPLIT_RE.split(s) if p.strip()]
  return parts if parts else ([s.strip()] if s.strip() else [])


_LETTER_SIZE_RE = re.compile(r"^(XXS|XS|S|M|L|XL|XXL|XXXL|\d?XL)$", re.I)
_NUMERIC_SIZE_RE = re.compile(r"^(\d{2})$")
_WAIST_RE = re.compile(r"^W\d{2,3}$", re.I)


def normalize_size_token(tok: str, *, size_system: str | None = None) -> str:
  t = str(tok).strip()
  if not t:
    return ""
  u = t.upper().replace(" ", "")
  if _WAIST_RE.match(u):
    return u
  if _LETTER_SIZE_RE.match(u):
    return u
  if _NUMERIC_SIZE_RE.match(u):
    return u
  return u


def normalize_sizes(raw: Any, *, size_system: str | None = None) -> list[str]:
  if raw is None:
    return []
  if isinstance(raw, str):
    items = _split_listish(raw)
  elif isinstance(raw, list):
    items = [str(x) for x in raw]
  else:
    items = [str(raw)]
  out: list[str] = []
  for x in items:
    s = normalize_size_token(x, size_system=size_system)
    if s and s not in out:
      out.append(s)
  return out


def normalize_size_system(raw: str | None) -> str | None:
  if raw is None or not str(raw).strip():
    return None
  s = str(raw).strip().upper().replace(" ", "_")
  aliases = {
    "INT": "INT",
    "INTERNATIONAL": "INT",
    "EU": "EU",
    "EURO": "EU",
    "US": "US",
    "USA": "US",
    "UK": "UK",
    "RU": "RU",
    "RUS": "RU",
    "LETTER": "LETTER",
    "LETTERS": "LETTER",
  }
  return aliases.get(s, s if len(s) <= 16 else s[:16])


def infer_size_system(sizes: list[str]) -> str | None:
  if not sizes:
    return None
  letters = 0
  nums = 0
  waist = 0
  for z in sizes:
    u = str(z).upper()
    if _WAIST_RE.match(u):
      waist += 1
    elif _LETTER_SIZE_RE.match(u):
      letters += 1
    elif _NUMERIC_SIZE_RE.match(u):
      nums += 1
  if waist >= max(1, len(sizes) // 2):
    return "US"
  if letters >= max(1, len(sizes) // 2):
    return "LETTER"
  if nums >= max(1, len(sizes) // 2):
    return "EU"
  return None
