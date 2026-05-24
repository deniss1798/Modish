from __future__ import annotations

import csv
import io
import re
import xml.etree.ElementTree as ET
from typing import Any


def _xml_local_tag(el: ET.Element) -> str:
  return (el.tag or "").split("}")[-1].lower()


def is_yml_catalog_xml(feed_content: str) -> bool:
  """Тело фида в стиле Яндекс.Маркета: <yml_catalog> или <shop>…<offers> (часто с расширением .xml)."""
  s = feed_content.lstrip("\ufeff").lstrip()
  head = s[:12000].lower()
  if "<yml_catalog" in head:
    return True
  if "<shop" in head and "<offers" in head and "<offer" in head:
    return True
  return False


def _param_row_key(param_name: str) -> str:
  key = (param_name or "").strip().lower()
  key = re.sub(r"\s+", "_", key)
  return f"param_{key}" if key else "param"


def _yml_category_map(shop_el: ET.Element) -> dict[str, str]:
  """<categories><category id="…">Название</category>…"""
  out: dict[str, str] = {}
  for block in shop_el:
    if _xml_local_tag(block) != "categories":
      continue
    for cat in block:
      if _xml_local_tag(cat) != "category":
        continue
      cid = (cat.attrib.get("id") or "").strip()
      name = "".join(cat.itertext()).strip()
      if cid:
        out[cid] = name
    break
  return out


def _yml_offer_to_row(el: ET.Element) -> dict[str, Any] | None:
  """Один <offer>: атрибуты + дочерние поля; несколько <picture> / <collectionId>; <param name>."""
  row: dict[str, Any] = {}
  for ak, av in (el.attrib or {}).items():
    k = str(ak).strip().lower()
    if k and av is not None and str(av).strip() != "":
      row[k] = str(av).strip()

  pictures: list[str] = []
  collection_ids: list[str] = []

  for child in el:
    tag = _xml_local_tag(child)
    if tag == "param":
      pname = (child.attrib.get("name") or "").strip()
      val = "".join(child.itertext()).strip()
      if pname and val:
        row[_param_row_key(pname)] = val
      continue
    if tag == "picture":
      val = (child.text or "").strip()
      if val:
        pictures.append(val)
      continue
    if tag == "collectionid":
      val = (child.text or "").strip()
      if val:
        collection_ids.append(val)
      continue
    if tag == "description":
      row["description"] = "".join(child.itertext()).strip()
      continue
    val = (child.text or "").strip()
    if not val:
      continue
    row[tag] = val

  if pictures:
    row["picture"] = pictures[0]
    row["pictures"] = pictures
  if collection_ids:
    row["collectionids"] = collection_ids

  if not row.get("id"):
    return None
  return row


def parse_yml_catalog_xml(feed_content: str) -> list[dict[str, Any]]:
  """
  YML-каталог (Яндекс.Маркет / Befree): корень yml_catalog/shop/offers/offer.
  id оффера, group_id, available — из атрибутов; param — в ключи param_*; все picture — в pictures.
  Имя категории подставляется из <categories> по categoryId.
  """
  out: list[dict[str, Any]] = []
  raw = feed_content.lstrip("\ufeff").lstrip()
  try:
    root = ET.fromstring(raw)
  except ET.ParseError:
    return out

  shop_el: ET.Element | None = None
  if _xml_local_tag(root) == "yml_catalog":
    for shop in root:
      if _xml_local_tag(shop) == "shop":
        shop_el = shop
        break
  elif _xml_local_tag(root) == "shop":
    shop_el = root

  cat_map: dict[str, str] = {}
  offers_el: ET.Element | None = None
  if shop_el is not None:
    cat_map = _yml_category_map(shop_el)
    for ch in shop_el:
      if _xml_local_tag(ch) == "offers":
        offers_el = ch
        break
  else:
    for el in root.iter():
      if _xml_local_tag(el) == "offers":
        offers_el = el
        break

  if offers_el is None:
    return out

  for offer in offers_el:
    if _xml_local_tag(offer) != "offer":
      continue
    row = _yml_offer_to_row(offer)
    if not row:
      continue
    cid = row.get("categoryid")
    if cid and cat_map:
      name = cat_map.get(str(cid).strip())
      if name:
        row["category_name"] = name
    out.append(row)
  return out


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


def _expand_admitad_param_column(row: dict[str, Any]) -> dict[str, Any]:
  """
  Admitad CSV: одна колонка param — «Цвет:черный|Размер:M|Размер:L».
  Раскладываем в param_цвет / param_размер и sizes для feed_row_mapper.
  """
  raw = row.get("param") or row.get("Param")
  if not raw or not isinstance(raw, str):
    return row
  out = dict(row)
  sizes: list[str] = []
  size_key = _param_row_key("размер")
  for part in raw.split("|"):
    piece = part.strip()
    if not piece or ":" not in piece:
      continue
    name, val = piece.split(":", 1)
    name = name.strip().lower()
    val = val.strip()
    if not name or not val:
      continue
    if name in ("размер", "size"):
      for piece_size in re.split(r"[,;]", val):
        s = piece_size.strip()
        if s:
          sizes.append(s)
      continue
    out[_param_row_key(name)] = val
  if sizes:
    out[size_key] = sizes[0]
    out["sizes"] = "|".join(sizes)
  return out


def parse_admitad_csv(feed_content: str) -> list[dict[str, Any]]:
  """CSV с заголовком (Admitad export_adv_products, разделитель ;)."""
  text = feed_content.lstrip("\ufeff")
  try:
    dialect = csv.Sniffer().sniff(text[:8192], delimiters=";,\t")
    delimiter = dialect.delimiter
  except csv.Error:
    delimiter = ";"
  reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
  rows: list[dict[str, Any]] = []
  for r in reader:
    if not r or all(not (v or "").strip() for v in r.values()):
      continue
    rows.append(_expand_admitad_param_column(dict(r)))
  return rows


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
