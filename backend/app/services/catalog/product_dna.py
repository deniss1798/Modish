"""Evidence-bearing Product DNA; pure, deterministic, no I/O or ORM mutation."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import re
from typing import Any

from .fashion_taxonomy import (
  INCOMPATIBLE_PAIRS,
  SINGLE_VALUE_DIMENSIONS,
  TAXONOMY,
  TAXONOMY_VERSION,
  evidence_matches,
  normalize_text,
)

DNA_VERSION = "product_dna_v1"
RULE_VERSION = "product_dna_rules_v3"
DEFAULT_RELIABLE_CONFIDENCE = 0.75

# Confidence is an explicit rule policy, NOT a measured probability.
CONFIDENCE = {"raw_attribute": 0.95, "sold_item_title": 0.80, "legacy_model": 0.35}

_RAW_KEYS = {
  "style": {"style", "style_tags", "стиль"},
  "color": {"color", "colors", "цвет"},
  "material": {"material", "composition", "материал", "состав"},
  "season": {"season", "сезон"},
  "fit": {"fit", "посадка", "крой"},
  "silhouette": {"silhouette", "силуэт", "крой"},
  "pattern": {"pattern", "print", "принт", "рисунок"},
  "length": {"length", "длина", "длина_изделия"},
  "sleeve": {"sleeve", "рукав", "длина_рукава", "тип_рукава"},
  "neckline": {"neckline", "вырез", "горловина", "тип_воротника"},
  "rise": {"rise", "высота_посадки"},
  "texture": {"texture", "фактура"},
  "occasion": {"occasion", "повод", "назначение"},
  "formality": {"formality", "формальность"},
}
_MODEL_FIELDS = {
  "style": ("style_tags",),
  "color": ("colors", "color_family", "color_original"),
  "material": ("material",),
  "season": ("season",),
  "fit": ("fit",),
  "silhouette": ("silhouette",),
  "pattern": (), "length": (), "sleeve": (), "neckline": (), "rise": (), "texture": (),
  "occasion": ("occasion",), "formality": (),
}
_COMMERCE_FIELDS = (
  "id", "external_id", "source", "source_id", "title", "brand", "category",
  "category_name", "subcategory", "gender_target", "price", "old_price", "currency",
  "available_sizes", "available_sizes_detailed", "size_original", "size_system",
  "is_available", "availability_status", "is_active", "is_deleted_from_feed",
  "last_seen_in_feed_at", "last_checked_at", "external_updated_at",
  "image_url", "image_urls", "product_url", "original_url", "affiliate_url",
)

# Sold-item noun must occur near the start. A second item, a reference to one,
# or an unrecognized item is not treated as an attribute-bearing title.
_ITEM = re.compile(
  r"(?<!\w)(?:рубашк[аи]|блуз[аы]|блузк[аи]|плать[ея]|юбк[аи]|брюки|джинсы|"
  r"футболк[аи]|топ|топы|шорты|джемпер|свитер|кардиган|водолазк[аи]|худи|"
  r"куртк[аи]|пальто|пуховик|ветровк[аи]|жакет|пиджак|жилет|комбинезон|"
  r"костюм|блейзер|пуловер|кроссовки|ботинки|туфли|сандалии|сапоги|лоферы|ремень|сумк[аи]|"
  r"shirt|blouse|dress|skirt|trousers|jeans|t[ -]shirt|top|shorts|sweater|"
  r"cardigan|hoodie|jacket|coat|blazer|vest|jumpsuit|suit|shoes|boots|belt|bag)(?!\w)"
)
_REFERENCE = re.compile(
  r"(?<!\w)(?:для|к|под|с|со|сочетайт\w*|дополнит\w*|рекомендуем|for|with|pair|wear\s+with)(?!\w)"
)


def _json_value(value: Any) -> Any:
  if value is None or isinstance(value, (str, bool, int, float)):
    return value
  if isinstance(value, (datetime, date)):
    return value.isoformat()
  if isinstance(value, Decimal):
    return str(value)
  if isinstance(value, dict):
    return {str(key): _json_value(value[key]) for key in sorted(value, key=str)}
  if isinstance(value, (list, tuple)):
    return [_json_value(item) for item in value]
  return str(value)


def _texts(value: Any) -> list[str]:
  if isinstance(value, str):
    return [value[:2048]] if value.strip() else []
  if isinstance(value, (list, tuple)):
    return [text for item in value for text in _texts(item)]
  return []


def _sold_item_title(title: Any) -> str:
  if not isinstance(title, str):
    return ""
  scope = normalize_text(re.split(r"[\n;.!?]", title, maxsplit=1)[0])
  scope = _REFERENCE.split(scope, maxsplit=1)[0].strip()
  items = list(_ITEM.finditer(scope))
  if len(items) != 1 or len(scope[:items[0].start()].split()) > 5:
    return ""
  return scope


def build_product_dna(product: Any) -> dict[str, Any]:
  """Snapshot model facts separately from bounded semantic assertions.

  Commerce values are copied, never validated, inferred, or used as proof of
  current availability/size. Legacy semantic columns retain explicit low-trust
  provenance even if an earlier importer happened to populate them.
  """
  return _build_product_dna(product, TAXONOMY)


def _raw_evidence(product: Any, dimensions) -> dict[str, list[tuple[str, str]]]:
  """Index each raw key once, retaining container/key order and provenance."""
  keys = {}
  for dimension in TAXONOMY:
    if dimension in dimensions:
      for key in _RAW_KEYS[dimension]:
        keys.setdefault(key, []).append(dimension)
  indexed = {dimension: [] for dimension in dimensions}
  for container_name in ("feed_raw_json", "raw_params_json"):
    raw = getattr(product, container_name, None)
    if not isinstance(raw, dict):
      continue
    for key in sorted(raw, key=str):
      normalized_key = normalize_text(str(key)).replace(" ", "_").removeprefix("param_")
      targets = keys.get(normalized_key)
      if not targets:
        continue
      texts = _texts(raw[key])
      if texts:
        for dimension in targets:
          indexed[dimension].append((" | ".join(texts), f"{container_name}.{key}"))
  return indexed


def _build_product_dna(product: Any, dimensions, *, include_commerce: bool = True) -> dict[str, Any]:
  assertions: dict[str, list[dict[str, Any]]] = {}
  blocked: dict[str, list[dict[str, Any]]] = {}
  title = _sold_item_title(getattr(product, "title", None))
  raw_evidence = _raw_evidence(product, dimensions)
  for dimension in TAXONOMY:
    if dimension not in dimensions:
      continue
    candidates: dict[str, dict[str, Any]] = {}
    evidence_sets: list[set[str]] = []

    def add(text: str, *, kind: str, field: str) -> None:
      matches, reasons = evidence_matches(dimension, text)
      if reasons and kind != "legacy_model":
        blocked.setdefault(dimension, []).append({
          "source": kind, "field": field, "raw_value": text,
          "reasons": reasons, "rule_version": RULE_VERSION,
        })
      if kind != "legacy_model" and matches:
        evidence_sets.append(set(matches))
      for value, matched in matches.items():
        evidence = {
          "source": kind,
          "field": field,
          "raw_value": text,
          "matched_text": matched,
          "rule_id": f"{kind}.{dimension}.{value}",
          "rule_version": RULE_VERSION,
          "confidence": CONFIDENCE[kind],
        }
        row = candidates.setdefault(value, {
          "value": value,
          "confidence": CONFIDENCE[kind],
          "rule_id": f"assertion.{dimension}.{value}",
          "rule_version": RULE_VERSION,
          "evidence": [],
        })
        row["confidence"] = max(row["confidence"], CONFIDENCE[kind])
        if evidence not in row["evidence"]:
          row["evidence"].append(evidence)

    for text, field in raw_evidence[dimension]:
      # One list attribute can intentionally contain multiple colours/materials.
      add(text, kind="raw_attribute", field=field)
    if title and dimension not in {"occasion", "formality"}:
      add(title, kind="sold_item_title", field="title")
    for field in _MODEL_FIELDS[dimension]:
      texts = _texts(getattr(product, field, None))
      if texts:
        add(" | ".join(texts), kind="legacy_model", field=field)

    supported = {value for value, row in candidates.items() if row["confidence"] >= DEFAULT_RELIABLE_CONFIDENCE}
    conflict = dimension in SINGLE_VALUE_DIMENSIONS and len(supported) > 1
    conflict |= bool(blocked.get(dimension))
    conflict |= any(pair <= supported for pair in INCOMPATIBLE_PAIRS.get(dimension, ()))
    # If two explicit sources disagree, do not silently choose the newest/first.
    conflict |= any(left.isdisjoint(right) for i, left in enumerate(evidence_sets) for right in evidence_sets[i + 1:])
    rows = []
    for value in sorted(candidates):
      row = candidates[value]
      row["status"] = (
        "conflict" if conflict and value in supported else
        "supported" if value in supported else "unverified"
      )
      rows.append(row)
    assertions[dimension] = rows

  result = {
    "schema_version": DNA_VERSION,
    "taxonomy_version": TAXONOMY_VERSION,
    "rules_version": RULE_VERSION,
    "confidence_meaning": "rule_policy_score_not_statistical_probability",
    "semantic": assertions,
    "blocked_evidence": blocked,
    "unknown_dimensions": [dim for dim, rows in assertions.items() if not any(row["status"] == "supported" for row in rows)],
  }
  if include_commerce:
    result["commerce"] = {
      "provenance": "model_snapshot_unverified",
      "fields": {field: _json_value(getattr(product, field, None)) for field in _COMMERCE_FIELDS},
    }
  return result


def reliable_taste_features(
  product: Any,
  *,
  dimensions: tuple[str, ...] | None = None,
  min_confidence: float = DEFAULT_RELIABLE_CONFIDENCE,
) -> dict[str, set[str]]:
  """Return supported semantic values only; never commerce or legacy fallbacks.

  Use dimensions=('material', 'fit', 'silhouette') for staged integration.
  This intentionally does not replace category/brand/item/price-band features.
  """
  if not 0 <= min_confidence <= 1:
    raise ValueError("min_confidence must be between 0 and 1")
  selected = set(dimensions if dimensions is not None else TAXONOMY)
  if selected - TAXONOMY.keys():
    raise ValueError("unknown Product DNA dimension")
  # Context and compatibility often need only one or two dimensions. Avoid
  # parsing all fourteen repeatedly while preserving exactly the same evidence.
  dna = _build_product_dna(product, selected, include_commerce=False)
  features = {}
  for dimension in TAXONOMY:
    if dimension not in selected:
      continue
    values = {
      row["value"] for row in dna["semantic"][dimension]
      if row["status"] == "supported" and row["confidence"] >= min_confidence
    }
    if values:
      features[dimension] = values
  return features
