"""Small, versioned vocabulary for rule-based Product DNA.

This is a deliberately limited dictionary, not a complete fashion ontology.
Patterns are bounded tokens/phrases; no substring colour/style matching.
"""
from __future__ import annotations

import re

TAXONOMY_VERSION = "fashion_taxonomy_v3"

# Canonical values align with consumers' existing feature keys where possible.
# Regexes may recognize grammatical endings, but never unrelated substrings.
PATTERNS: dict[str, dict[str, str]] = {
  "style": {
    "casual": r"casual|повседневн\w*",
    "smart_casual": r"smart[ _-]casual",
    "minimalism": r"minimalism|минимализм|минималистич\w*",
    "business": r"business|делов\w*|офисн\w*",
    "sporty": r"sporty|спортивн\w*|тренировочн\w*",
    "romantic": r"romantic|романтич\w*",
    "streetwear": r"streetwear|стритвир",
    "elegant": r"elegant|элегантн\w*|вечерн\w*",
  },
  "color": {
    "black": r"black|черн\w*",
    "white": r"white|бел(?:ый|ая|ое|ые|ого|ой|ых|ую|ом)",
    "cream": r"cream|ivory|beige|ecru|кремов\w*|молочн\w*|бежев\w*|экрю",
    "navy": r"navy|темно[ -]син\w*",
    "blue": r"blue|голуб\w*|син(?:ий|яя|ее|ие|его|ей|их|юю|ем)",
    "grey": r"grey|gray|graphite|сер(?:ый|ая|ое|ые|ого|ой|ых|ую|ом)|графитов\w*",
    "brown": r"brown|коричнев\w*",
    "green": r"green|olive|зелен\w*|хаки|оливков\w*",
    "burgundy": r"burgundy|bordeaux|бордов\w*|бордо",
    "red": r"red|красн\w*",
    "pink": r"pink|розов\w*",
    "yellow": r"yellow|желт\w*",
    "orange": r"orange|оранжев\w*",
    "purple": r"purple|violet|фиолетов\w*|сиренев\w*",
    "multicolor": r"multicolor|разноцветн\w*|мультиколор",
  },
  "material": {
    "cotton": r"cotton|хлопок|хлопк\w*|хлопчатобумажн\w*",
    "linen": r"linen|лен|льна|льнян\w*",
    "wool": r"wool|шерсть|шерст\w*",
    "silk": r"silk|шелк|шелк\w*",
    "viscose": r"viscose|вискоз\w*",
    "polyester": r"polyester|полиэстер\w*",
    "leather": r"leather|кожа|кожи|кожан\w*",
    "faux_leather": r"faux[ _-]leather|искусственн\w*\s+кож\w*|экокож\w*",
    "cashmere": r"cashmere|кашемир\w*",
  },
  "season": {
    "winter": r"winter|зимн\w*",
    "summer": r"summer|летн\w*",
    "demiseason": r"demiseason|demi[ -]season|демисезонн\w*|весна[ /-]осень|осень[ /-]весна",
    "all_season": r"all[ _-]season|всесезонн\w*|круглогодичн\w*",
  },
  "fit": {
    "slim": r"slim(?:[ -]fit)?|приталенн\w*|облегающ\w*|прилегающ\w*|по\s+фигуре",
    "regular": r"regular(?:[ -]fit)?|стандартн\w*\s+посадк\w*",
    "relaxed": r"relaxed(?:[ -]fit)?|(?:свободн\w*|расслабленн\w*)\s+(?:кро\w*|посадк\w*|силуэт\w*)|^свободн(?:ый|ая|ое|ые)$",
    "oversized": r"oversized?|оверсайз",
  },
  "silhouette": {
    "straight": (
      r"straight|^прям(?:ой|ая|ое|ые)$|"
      r"прям\w*\s+(?:облегающ\w*\s+)?(?:кро\w*|силуэт\w*)|"
      r"прям(?:ая|ое|ые)\s+(?:юбк[аи]|плать[ея]|брюки|джинсы)|"
      r"(?:юбк[аи]|плать[ея]|брюки|джинсы)(?:[ -](?:мини|миди|макси))?\s+прям(?:ая|ое|ые)"
    ),
    "a_line": r"a[ _-]line|а[ -]силуэт\w*|трапециевидн\w*",
    "flared": r"flared|клеш|расклешенн\w*",
    "tapered": r"tapered|зауженн\w*",
    "wide": r"wide(?:[ -]leg)?|широк\w*\s+(?:брюк\w*|штанин\w*|кро\w*|силуэт\w*)",
  },
  "pattern": {
    "solid": r"solid|однотонн\w*",
    "striped": r"striped|полосат\w*|в\s+полоску",
    "checked": r"checked|checkered|клетчат\w*|в\s+клетку",
    "floral": r"floral|цветочн\w*\s+(?:принт\w*|рисунк\w*)",
    "polka_dot": r"polka[ _-]dot|в\s+горошек",
    "animal": r"animal[ _-]print|леопардов\w*\s+принт\w*|зебров\w*\s+принт\w*",
  },
  "length": {
    "mini": r"mini|мини",
    "midi": r"midi|миди",
    "maxi": r"maxi|maxi[ -]length|макси",
  },
  "sleeve": {
    "sleeveless": r"sleeveless|without\s+sleeves|без\s+рукавов",
    "short": r"short[ _-]sleeves?|коротк\w*\s+рукав\w*",
    "long": r"long[ _-]sleeves?|длинн\w*\s+рукав\w*",
    "three_quarter": r"three[ _-]quarter[ _-]sleeves?|рукав\w*\s+3/4",
  },
  "neckline": {
    "v_neck": r"v[ _-]neck|v[ -]образн\w*\s+вырез\w*",
    "round": r"round[ _-]neck|crew[ _-]neck|кругл\w*\s+(?:вырез\w*|горловин\w*)",
    "high": r"turtleneck|high[ _-]neck|высок\w*\s+(?:ворот\w*|горловин\w*)",
    "collared": r"collared|отложн\w*\s+воротник\w*",
  },
  "rise": {
    "high": r"high[ _-]rise|высок\w*\s+посадк\w*",
    "mid": r"mid[ _-]rise|средн\w*\s+посадк\w*",
    "low": r"low[ _-]rise|низк\w*\s+посадк\w*",
  },
  "texture": {
    "smooth": r"smooth|гладк\w*\s+(?:фактур\w*|ткан\w*)",
    "ribbed": r"ribbed|в\s+рубчик",
    "knitted": r"knitted|вязан\w*",
    "quilted": r"quilted|стеган\w*",
    "pleated": r"pleated|плиссе|плиссирован\w*",
  },
  "occasion": {
    "work": r"work|office|офис|работа|для\s+офиса",
    "date": r"date|свидание|для\s+свидания",
    "evening": r"evening|party|вечер|вечеринка",
    "vacation": r"vacation|отпуск|для\s+отпуска",
    "walk": r"walk|прогулка|для\s+прогулок",
    "wedding": r"wedding|свадьба|для\s+свадьбы",
  },
  "formality": {
    "casual": r"casual|повседневный",
    "smart": r"smart[ _-]casual|smart|нарядный",
    "formal": r"formal|формальный|торжественный",
  },
}

TAXONOMY = {dimension: tuple(values) for dimension, values in PATTERNS.items()}
SINGLE_VALUE_DIMENSIONS = frozenset({"fit", "silhouette", "season", "length", "sleeve", "neckline", "rise", "formality"})
INCOMPATIBLE_PAIRS = {"style": (frozenset({"sporty", "business"}),)}
_COMPILED = {dim: {value: re.compile(r"(?<!\w)(?:" + pattern + r")(?!\w)")
                  for value, pattern in patterns.items()} for dim, patterns in PATTERNS.items()}


def normalize_text(value: str) -> str:
  return re.sub(r"\s+", " ", value.lower().replace("ё", "е")).strip()


def _lexical_matches(dimension: str, text: str) -> dict[str, str]:
  normalized = normalize_text(text)
  return _normalized_matches(dimension, normalized)


def _normalized_matches(dimension: str, normalized: str) -> dict[str, str]:
  patterns = _COMPILED[dimension]
  if normalized in patterns:
    return {normalized: normalized}
  matches = {}
  for value, pattern in patterns.items():
    match = pattern.search(normalized)
    if match:
      matches[value] = match.group(0)
  if "faux_leather" in matches:
    matches.pop("leather", None)
  if dimension == "color" and "navy" in matches:
    # 'темно-синий' is one colour, not a second blue assertion.
    if matches.get("blue", "").startswith("син"):
      matches.pop("blue", None)
  if dimension == "style" and "smart_casual" in matches:
    matches.pop("casual", None)
  if dimension == "formality" and "smart" in matches:
    matches.pop("casual", None)
  return matches


def evidence_matches(dimension: str, text: str) -> tuple[dict[str, str], list[str]]:
  """Keep negative evidence so another field cannot silently override it.

  V1 leaves the entire dimension unresolved when linguistic scope is unclear.
  A zero composition percentage also does not establish a present material.
  """
  normalized = normalize_text(text)
  matches = _normalized_matches(dimension, normalized)
  if not matches:
    return {}, []
  reasons = []
  # An explicit absence of sleeves does not negate colour, fibre or fit.
  # Other ambiguous negations remain conservative until separately reviewed.
  negation_scope = re.sub(r"(?<!\w)(?:без\s+рукавов|without\s+sleeves)(?!\w)", "", normalized)
  if re.search(r"(?<!\w)(?:не|без|no|not|without)(?!\w)", negation_scope):
    reasons.append("negated_dimension")
  if dimension == "material" and re.search(r"(?<![\d.,])0+(?:[.,]0+)?\s*%", normalized):
    reasons.append("zero_material_percentage")
  return ({} if reasons else matches), reasons


def blocked_evidence(dimension: str, text: str) -> list[str]:
  """Reasons that prevent a positive assertion in this dimension."""
  return evidence_matches(dimension, text)[1]


def matched_values(dimension: str, text: str) -> dict[str, str]:
  """Positive bounded matches only; callers must retain blocked_evidence too."""
  return evidence_matches(dimension, text)[0]
