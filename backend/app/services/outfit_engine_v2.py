"""Outfit Engine v2.

Builds outfits from personalized product candidates, then scores each outfit
by personal relevance, item compatibility, budget, and diversity.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from itertools import product as cartesian_product
from typing import Iterable
from uuid import uuid4

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from ..catalog_normalize import normalize_category
from ..models import FitProfile, Outfit, Product, RecommendationEventV2, User
from .catalog.catalog_quality import product_is_feed_eligible
from .catalog.rule_filters import load_rules_by_source_id, product_gender_compatible, product_passes_source_rules
from .feed_filters import product_passes_budget, product_passes_size
from .recommendation_config import OUTFIT_ENGINE_VERSION, OUTFIT_SCORE_WEIGHTS
from .recommendation_engine import build_feed_context, generate_feed, score_product


SLOT_CATEGORIES: dict[str, set[str]] = {
  "one_piece": {"платья", "костюмы", "комбинезоны"},
  "top": {"футболки", "рубашки", "верхний_слой", "джемперы", "худи"},
  "bottom": {"джинсы", "брюки", "юбки", "шорты"},
  "shoes": {"обувь"},
  "accessory": {"аксессуары", "сумки"},
}

SCENARIO_SLOT_OPTIONS: dict[str, list[tuple[str, ...]]] = {
  "daily": [("top", "bottom", "shoes"), ("one_piece", "shoes")],
  "office": [("top", "bottom", "shoes"), ("one_piece", "shoes")],
  "date": [("one_piece", "shoes"), ("top", "bottom", "shoes")],
  "restaurant": [("one_piece", "shoes"), ("top", "bottom", "shoes")],
  "wedding": [("one_piece", "shoes"), ("top", "bottom", "shoes")],
  "party": [("one_piece", "shoes"), ("top", "bottom", "shoes")],
  "evening": [("one_piece", "shoes"), ("top", "bottom", "shoes")],
  "gym": [("top", "bottom", "shoes")],
  "run": [("top", "bottom", "shoes")],
  "vacation": [("top", "bottom", "shoes"), ("one_piece", "shoes")],
}

SCENARIO_LABELS = {
  "daily": "Каждый день",
  "office": "Офис",
  "date": "Свидание",
  "restaurant": "Ресторан",
  "wedding": "Свадьба",
  "party": "Вечеринка",
  "evening": "Вечер",
  "gym": "Спортзал",
  "run": "Пробежка",
  "vacation": "Отпуск",
}

NEUTRAL_COLORS = {"black", "white", "gray", "grey", "beige", "navy", "коричневый", "черный", "белый", "серый"}
KIDS_TITLE_MARKERS = ("детск", "для детей", "kids", "kid ", "junior", "малыш", "мальчик", "девочк")
OFFICE_AVOID_IN_TITLE = ("бокс", "boxing", "everlast", "борц", "штанга", "фитнес-перчат")
SKIP_PENALTY = 0.08
SKIP_PENALTY_COOLDOWN_HOURS = 24


@dataclass(frozen=True)
class SlotCandidate:
  slot: str
  product: Product
  personal_score: float
  fit_score: float
  taste_score: float
  context_score: float
  skip_penalty: float = 0.0


@dataclass(frozen=True)
class OutfitCandidate:
  items: dict[str, SlotCandidate]
  total_price: int
  personal_score: float
  compatibility_score: float
  novelty_score: float
  final_score: float
  reasons: list[str]


def _clamp01(value: float) -> float:
  return max(0.0, min(1.0, float(value)))


def _scenario_key(scenario: str | None) -> str:
  key = (scenario or "daily").strip().lower() or "daily"
  aliases = {"casual": "daily", "minimal": "daily"}
  return aliases.get(key, key if key in SCENARIO_SLOT_OPTIONS else "daily")


def _product_categories(product: Product) -> set[str]:
  cats: set[str] = set()
  for raw in (product.category, product.category_name, product.subcategory):
    value = normalize_category(str(raw or "").strip())
    if value:
      cats.add(value)
  return cats


def product_slot(product: Product) -> str | None:
  cats = _product_categories(product)
  title = (product.title or "").lower()
  if cats & SLOT_CATEGORIES["one_piece"] or any(word in title for word in ("плать", "комбинезон", "suit")):
    return "one_piece"
  for slot in ("top", "bottom", "shoes", "accessory"):
    if cats & SLOT_CATEGORIES[slot]:
      return slot
  if any(k in title for k in ("сумк", "bag", "аксессуар", "accessory")):
    return "accessory"
  return None


def _scenario_product_ok(product: Product, scenario: str) -> bool:
  title = (product.title or "").lower()
  if any(marker in title for marker in KIDS_TITLE_MARKERS):
    return False
  if scenario in {"office", "restaurant", "wedding"} and any(marker in title for marker in OFFICE_AVOID_IN_TITLE):
    return False
  return int(product.price or 0) <= 500_000


def _hard_ok(product: Product, *, fit: FitProfile | None, rules_by_source: dict) -> bool:
  if not product_is_feed_eligible(product):
    return False
  if not product_passes_source_rules(product, rules_by_source):
    return False
  if fit and not product_gender_compatible(product, fit.gender_target):
    return False
  if not product_passes_budget(product, fit):
    return False
  if not product_passes_size(product, fit):
    return False
  return True


def _excluded_product_ids(db: Session, user: User, *, scenario: str) -> set[str]:
  ids = set(
    db.execute(
      select(RecommendationEventV2.product_id).where(
        RecommendationEventV2.user_id == user.id,
        RecommendationEventV2.product_id.is_not(None),
        RecommendationEventV2.event_type.in_(["dislike", "post_purchase_negative"]),
      )
    ).scalars()
  )
  rows = db.execute(select(Outfit).where(Outfit.user_id == user.id, Outfit.is_saved == 0)).scalars().all()
  for outfit in rows:
    if (outfit.style_direction or "").strip().lower() == scenario:
      continue
    for pid in (outfit.items_json or {}).values():
      if pid:
        ids.add(str(pid))
  return {str(pid) for pid in ids if str(pid).strip()}


def _recent_skip_product_ids(db: Session, user: User) -> set[str]:
  cutoff = datetime.now(timezone.utc) - timedelta(hours=SKIP_PENALTY_COOLDOWN_HOURS)
  ids = db.execute(
    select(RecommendationEventV2.product_id).where(
      RecommendationEventV2.user_id == user.id,
      RecommendationEventV2.product_id.is_not(None),
      RecommendationEventV2.event_type == "skip",
      RecommendationEventV2.created_at >= cutoff,
    )
  ).scalars()
  return {str(pid) for pid in ids if str(pid).strip()}


def _skip_penalty(product_id: str, skipped_ids: set[str]) -> float:
  return SKIP_PENALTY if product_id in skipped_ids else 0.0


def _slot_category_conditions(slots: Iterable[str]):
  cats: set[str] = set()
  for slot in slots:
    cats |= SLOT_CATEGORIES.get(slot, set())
  return or_(Product.category.in_(list(cats)), Product.category_name.in_(list(cats)))


def _catalog_slot_candidates(
  db: Session,
  user: User,
  *,
  slots: set[str],
  scenario: str,
  excluded: set[str],
  existing_ids: set[str],
  limit: int,
) -> list[Product]:
  del user
  rows = db.execute(
    select(Product)
    .where(
      Product.is_active == 1,
      Product.is_available == 1,
      Product.is_deleted_from_feed == 0,
      Product.source != "demo",
      _slot_category_conditions(slots),
    )
    .order_by(Product.updated_at.desc(), Product.created_at.desc(), Product.id.asc())
    .limit(max(60, limit * 8))
  ).scalars().all()
  out: list[Product] = []
  for product in rows:
    if product.id in excluded or product.id in existing_ids:
      continue
    if product_slot(product) not in slots:
      continue
    if not _scenario_product_ok(product, scenario):
      continue
    out.append(product)
    if len(out) >= limit:
      break
  return out


def _build_slot_candidates(
  db: Session,
  user: User,
  *,
  scenario: str,
  fit: FitProfile | None,
  excluded: set[str],
  skipped_ids: set[str],
  per_slot: int,
) -> dict[str, list[SlotCandidate]]:
  slots = set().union(*[set(option) for option in SCENARIO_SLOT_OPTIONS[scenario]]) | {"accessory"}
  buckets: dict[str, list[SlotCandidate]] = {slot: [] for slot in slots}
  seen: set[str] = set()
  rules_by_source = load_rules_by_source_id(db)

  scored_feed = generate_feed(
    db,
    user,
    limit=120,
    exclude_product_ids=excluded,
    scenario=scenario,
    max_to_score=900,
  )
  for scored in scored_feed:
    slot = product_slot(scored.product)
    if slot not in buckets or scored.product.id in seen:
      continue
    if not _scenario_product_ok(scored.product, scenario):
      continue
    penalty = _skip_penalty(scored.product.id, skipped_ids)
    buckets[slot].append(
      SlotCandidate(
        slot=slot,
        product=scored.product,
        personal_score=_clamp01(float(scored.final_score) - penalty),
        fit_score=float(scored.breakdown.get("fit_score", 0.0)),
        taste_score=float(scored.breakdown.get("taste_score", 0.0)),
        context_score=float(scored.breakdown.get("context_score", 0.0)),
        skip_penalty=penalty,
      )
    )
    seen.add(scored.product.id)

  missing = {slot for slot, values in buckets.items() if len(values) < per_slot}
  if missing:
    rows = _catalog_slot_candidates(
      db,
      user,
      slots=missing,
      scenario=scenario,
      excluded=excluded,
      existing_ids=seen,
      limit=per_slot * max(1, len(missing)) * 3,
    )
    rows = [product for product in rows if _hard_ok(product, fit=fit, rules_by_source=rules_by_source)]
    ctx = build_feed_context(db, user, product_ids=[product.id for product in rows], scenario=scenario)
    for product in rows:
      slot = product_slot(product)
      if slot not in buckets or len(buckets[slot]) >= per_slot:
        continue
      scored = score_product(db, user, product, ctx)
      if scored.final_score <= 0:
        continue
      penalty = _skip_penalty(product.id, skipped_ids)
      buckets[slot].append(
        SlotCandidate(
          slot=slot,
          product=product,
          personal_score=_clamp01(float(scored.final_score) - penalty),
          fit_score=float(scored.breakdown.get("fit_score", 0.0)),
          taste_score=float(scored.breakdown.get("taste_score", 0.0)),
          context_score=float(scored.breakdown.get("context_score", 0.0)),
          skip_penalty=penalty,
        )
      )
      seen.add(product.id)

  for slot in buckets:
    buckets[slot].sort(key=lambda item: item.personal_score, reverse=True)
    buckets[slot] = buckets[slot][:per_slot]
  return buckets


def _colors(product: Product) -> set[str]:
  out = {str(c).strip().lower() for c in (product.colors or []) if str(c).strip()}
  if product.color_family:
    out.add(str(product.color_family).strip().lower())
  return out


def _styles(product: Product) -> set[str]:
  return {str(x).strip().lower() for x in (product.style_tags or []) if str(x).strip()}


def _formality(product: Product) -> int:
  text = " ".join(
    [
      str(product.occasion or ""),
      str(product.category or ""),
      str(product.title or ""),
      " ".join(str(x) for x in (product.style_tags or [])),
    ]
  ).lower()
  if any(x in text for x in ("gym", "run", "спорт", "кроссов", "шорты")):
    return 0
  if any(x in text for x in ("office", "business", "formal", "офис", "костюм")):
    return 2
  if any(x in text for x in ("wedding", "evening", "party", "плать", "вечер")):
    return 3
  return 1


def compatibility_score(items: list[SlotCandidate], *, scenario: str) -> tuple[float, dict[str, float]]:
  # TODO: Replace these heuristics with a learned outfit-compatibility model once outfit feedback is dense enough.
  products = [item.product for item in items]
  color_sets = [_colors(product) for product in products if _colors(product)]
  all_colors = set().union(*color_sets) if color_sets else set()
  neutral_count = len(all_colors & NEUTRAL_COLORS)
  color_score = 0.62
  if not all_colors:
    color_score = 0.55
  elif len(all_colors) <= 3 or neutral_count:
    color_score = 0.82
  elif len(all_colors) >= 5:
    color_score = 0.45

  style_sets = [_styles(product) for product in products if _styles(product)]
  common_styles = set.intersection(*style_sets) if len(style_sets) >= 2 else set()
  style_score = 0.78 if common_styles else 0.58 if style_sets else 0.55

  formalities = [_formality(product) for product in products]
  formality_score = 1.0 - min(1.0, (max(formalities) - min(formalities)) / 3.0) if formalities else 0.55

  seasons = {str(product.season or "").strip().lower() for product in products if str(product.season or "").strip()}
  season_score = 0.80 if len(seasons) <= 1 else 0.60

  silhouettes = {str(product.silhouette or "").strip().lower() for product in products if str(product.silhouette or "").strip()}
  silhouette_score = 0.72
  if len(silhouettes) == 1 and any(x in silhouettes for x in ("oversized", "relaxed")) and len(products) >= 3:
    silhouette_score = 0.55
  elif silhouettes:
    silhouette_score = 0.78

  occasion_hits = 0
  occasion_known = 0
  for product in products:
    occasion = str(product.occasion or "").strip().lower()
    if not occasion:
      continue
    occasion_known += 1
    if occasion == scenario:
      occasion_hits += 1
  occasion_score = (occasion_hits / occasion_known) if occasion_known else 0.58
  if scenario == "daily":
    occasion_score = max(occasion_score, 0.65)

  parts = {
    "color_harmony": _clamp01(color_score),
    "style_consistency": _clamp01(style_score),
    "formality_compatibility": _clamp01(formality_score),
    "season_compatibility": _clamp01(season_score),
    "silhouette_balance": _clamp01(silhouette_score),
    "occasion_compatibility": _clamp01(occasion_score),
  }
  return _clamp01(sum(parts.values()) / len(parts)), parts


def _personal_score(items: list[SlotCandidate]) -> float:
  if not items:
    return 0.0
  values = []
  for item in items:
    values.append(_clamp01(((item.fit_score + item.taste_score + item.context_score) / 3.0) - item.skip_penalty))
  return _clamp01(sum(values) / len(values))


def _novelty_score(items: list[SlotCandidate]) -> float:
  if not items:
    return 0.0
  brands = {str(item.product.brand or "").strip().lower() for item in items if str(item.product.brand or "").strip()}
  cats = {normalize_category(item.product.category or item.product.category_name or "") for item in items}
  unique_ratio = (len(brands) + len(cats)) / max(1, len(items) * 2)
  return _clamp01(0.45 + 0.45 * unique_ratio)


def _outfit_budget_ok(total: int, item_count: int, fit: FitProfile | None) -> bool:
  if fit is None or int(fit.budget_max or 0) <= 0:
    return True
  return total <= int(fit.budget_max * max(1, item_count) * 1.05)


def _reason(candidate: OutfitCandidate, *, scenario: str, compatibility_parts: dict[str, float]) -> str:
  label = SCENARIO_LABELS.get(scenario, "Образ")
  reasons = [
    f"Сценарий: {label}",
    f"PersonalScore {candidate.personal_score:.2f}",
    f"CompatibilityScore {candidate.compatibility_score:.2f}",
  ]
  if compatibility_parts.get("color_harmony", 0.0) >= 0.75:
    reasons.append("цвета сочетаются")
  if compatibility_parts.get("style_consistency", 0.0) >= 0.70:
    reasons.append("стиль вещей согласован")
  if candidate.personal_score >= 0.65:
    reasons.append("товары близки к вашему профилю")
  reasons.append(f"engine: {OUTFIT_ENGINE_VERSION}")
  return ". ".join(reasons) + "."


def _build_combinations(
  buckets: dict[str, list[SlotCandidate]],
  *,
  scenario: str,
  fit: FitProfile | None,
  limit: int,
) -> list[OutfitCandidate]:
  candidates: list[OutfitCandidate] = []
  for required_slots in SCENARIO_SLOT_OPTIONS[scenario]:
    if any(not buckets.get(slot) for slot in required_slots):
      continue
    accessory_options: list[SlotCandidate | None] = [None] + buckets.get("accessory", [])[:3]
    slot_options = [buckets[slot][:6] for slot in required_slots] + [accessory_options]
    for combo in cartesian_product(*slot_options):
      slot_items = [item for item in combo if item is not None]
      product_ids = [item.product.id for item in slot_items]
      if len(product_ids) != len(set(product_ids)):
        continue
      items_by_slot = {item.slot: item for item in slot_items}
      total = sum(int(item.product.price or 0) for item in slot_items)
      if not _outfit_budget_ok(total, len(slot_items), fit):
        continue
      compat, compat_parts = compatibility_score(slot_items, scenario=scenario)
      personal = _personal_score(slot_items)
      novelty = _novelty_score(slot_items)
      final = _clamp01(
        OUTFIT_SCORE_WEIGHTS["personal_score"] * personal
        + OUTFIT_SCORE_WEIGHTS["compatibility_score"] * compat
        + OUTFIT_SCORE_WEIGHTS["novelty_score"] * novelty
      )
      candidate = OutfitCandidate(
        items=items_by_slot,
        total_price=total,
        personal_score=personal,
        compatibility_score=compat,
        novelty_score=novelty,
        final_score=final,
        reasons=[],
      )
      candidates.append(
        OutfitCandidate(
          items=items_by_slot,
          total_price=total,
          personal_score=personal,
          compatibility_score=compat,
          novelty_score=novelty,
          final_score=final,
          reasons=[_reason(candidate, scenario=scenario, compatibility_parts=compat_parts)],
        )
      )
    if candidates:
      break
  candidates.sort(key=lambda item: item.final_score, reverse=True)
  return candidates[: max(limit * 4, limit)]


def _diverse_top(candidates: list[OutfitCandidate], *, limit: int) -> list[OutfitCandidate]:
  out: list[OutfitCandidate] = []
  seen_signatures: set[tuple[str, ...]] = set()
  used_hero_ids: set[str] = set()
  for candidate in candidates:
    signature = tuple(sorted(item.product.id for item in candidate.items.values()))
    if signature in seen_signatures:
      continue
    hero = candidate.items.get("one_piece") or candidate.items.get("top") or candidate.items.get("bottom")
    if hero and hero.product.id in used_hero_ids and len(out) < limit:
      continue
    seen_signatures.add(signature)
    if hero:
      used_hero_ids.add(hero.product.id)
    out.append(candidate)
    if len(out) >= limit:
      break
  if len(out) < limit:
    for candidate in candidates:
      signature = tuple(sorted(item.product.id for item in candidate.items.values()))
      if signature in seen_signatures:
        continue
      out.append(candidate)
      if len(out) >= limit:
        break
  return out


def generate_outfits_v2(
  db: Session,
  user: User,
  *,
  count: int = 3,
  scenario: str = "daily",
  replace_existing: bool = True,
) -> list[Outfit]:
  scenario_key = _scenario_key(scenario)
  need = max(1, min(10, int(count)))
  fit = db.execute(select(FitProfile).where(FitProfile.user_id == user.id)).scalar_one_or_none()
  excluded = _excluded_product_ids(db, user, scenario=scenario_key)
  skipped_ids = _recent_skip_product_ids(db, user)

  buckets = _build_slot_candidates(
    db,
    user,
    scenario=scenario_key,
    fit=fit,
    excluded=excluded,
    skipped_ids=skipped_ids,
    per_slot=10,
  )
  combinations = _build_combinations(buckets, scenario=scenario_key, fit=fit, limit=need)
  selected = _diverse_top(combinations, limit=need)
  if not selected:
    return []

  if replace_existing:
    db.execute(
      delete(Outfit).where(
        Outfit.user_id == user.id,
        Outfit.style_direction == scenario_key,
        Outfit.is_saved == 0,
      )
    )
    db.flush()

  now = datetime.now(timezone.utc)
  created: list[Outfit] = []
  for candidate in selected:
    items_json = {slot: item.product.id for slot, item in candidate.items.items()}
    outfit = Outfit(
      id=str(uuid4()),
      user_id=user.id,
      items_json=items_json,
      total_price=candidate.total_price,
      style_direction=scenario_key,
      reason=candidate.reasons[0] if candidate.reasons else f"engine: {OUTFIT_ENGINE_VERSION}.",
      score=candidate.final_score,
      is_saved=0,
      created_at=now,
      updated_at=now,
    )
    db.add(outfit)
    created.append(outfit)

  db.flush()
  return created
