from __future__ import annotations

import csv
import io
import xml.etree.ElementTree as ET
from typing import Any


def parse_admitad_xml(feed_content: str) -> list[dict[str, Any]]:
  """Разбор типичного XML shop/offers (упрощённо: первый уровень offer/item)."""
  out: list[dict[str, Any]] = []
  try:
    root = ET.fromstring(feed_content)
  except ET.ParseError:
    return out
  for el in root.iter():
    tag = (el.tag or "").split("}")[-1].lower()
    if tag in ("offer", "item", "product"):
      row: dict[str, Any] = {}
      for child in el:
        ck = (child.tag or "").split("}")[-1].lower()
        if child.text and child.text.strip():
          row[ck] = child.text.strip()
      if row:
        out.append(row)
  return out


def parse_admitad_csv(feed_content: str) -> list[dict[str, Any]]:
  """CSV с заголовком (Admitad export)."""
  reader = csv.DictReader(io.StringIO(feed_content))
  return [dict(r) for r in reader if r]


def parse_admitad_yml(feed_content: str) -> list[dict[str, Any]]:
  """
  YML (Яндекс-маркет стиль): минимальный разбор offer-блоков без полного YAML.
  Для полного YML позже: отдельный парсер / PyYAML.
  """
  out: list[dict[str, Any]] = []
  try:
    import yaml  # type: ignore

    data = yaml.safe_load(feed_content)
    if isinstance(data, dict):
      offers = data.get("offers") or data.get("shop", {}).get("offers", [])
      if isinstance(offers, list):
        for o in offers:
          if isinstance(o, dict):
            out.append(o)
    return out
  except Exception:
    pass
  # fallback: грубый split по <offer
  if "<offer" in feed_content.lower():
    parts = feed_content.split("<offer")
    for p in parts[1:]:
      out.append({"_raw_fragment": p[:2000]})
  return out
