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

from ..catalog_normalize import normalize_category, normalize_colors_value
from ..models import FitProfile, MetricEvent, Outfit, Product, RecommendationEventV2, User
from .catalog.catalog_quality import product_is_feed_eligible
from .catalog.rule_filters import load_rules_by_source_id, product_gender_compatible, product_passes_source_rules
from .feed_filters import product_passes_budget, product_passes_size
from .recommendation_config import OUTFIT_ENGINE_VERSION, OUTFIT_SCORE_WEIGHTS
from .recommendation_engine import build_feed_context, generate_feed, score_product
from .product_identity import product_identity_keys
from .outfit_quality import FORMAL_SCENARIOS, FORMAL_SHOE_TERMS, garment_text, scenario_product_ok, pair_compatible, outfit_compatible
from .garment_roles import product_role
from .catalog_audience import adult_catalog_conditions


SLOT_CATEGORIES: dict[str, set[str]] = {
  "one_piece": {"платья"},
  "top": {"футболки", "рубашки", "верхний_слой", "джемперы", "худи"},
  "bottom": {"джинсы", "брюки", "юбки", "шорты"},
  "shoes": {"обувь"},
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
  return product_role(product)


def _scenario_product_ok(product: Product, scenario: str) -> bool:
  return scenario_product_ok(product, scenario)


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
  title_terms = {
    "top": ["рубаш", "блуз", "джемпер", "свитер", "лонгслив", "футболк", "топ ", "пуловер", "водолаз"],
    "bottom": ["брюк", "джинс", "юбк", "шорт"],
    "one_piece": ["плать", "сарафан"],
    "shoes": ["туфл", "лофер", "мокасин", "ботин", "ботильон", "балетк", "сапог", "кед", "кроссов"],
  }
  conditions = [Product.category.in_(list(cats)), Product.category_name.in_(list(cats))]
  for slot in slots:
    conditions.extend(Product.title.ilike(f"%{term}%") for term in title_terms.get(slot, []))
  return or_(*conditions)


def _catalog_slot_candidates(
  db: Session,
  user: User,
  *,
  slots: set[str],
  scenario: str,
  excluded: set[str],
  existing_ids: set[str],
  limit: int,
  anchor: Product | None = None,
  excluded_keys: set[str] | None = None,
) -> list[Product]:
  fit = db.execute(select(FitProfile).where(FitProfile.user_id == user.id)).scalar_one_or_none()
  rules = load_rules_by_source_id(db)
  out: list[Product] = []
  seen_keys: set[str] = set(excluded_keys or ())
  hidden = excluded | existing_ids
  if hidden:
    for p in db.execute(select(Product).where(Product.id.in_(hidden))).scalars():
      seen_keys.update(product_identity_keys(p))
  # Retrieve each role independently: a recent run of tops or size variants
  # must not crowd all shoes/bottoms out of the bounded candidate pool.
  per_role = 10
  for slot in sorted(slots):
    found = 0
    query = select(Product).where(
      Product.is_active == 1, Product.is_available == 1,
      Product.is_deleted_from_feed == 0, Product.source != "demo",
      Product.price > 0, _slot_category_conditions({slot}), *adult_catalog_conditions(),
    ).order_by(Product.updated_at.desc(), Product.created_at.desc(), Product.id.asc())
    if fit and fit.gender_target in {"menswear", "womenswear"}:
      opposite = "menswear" if fit.gender_target == "womenswear" else "womenswear"
      query = query.where(or_(Product.gender_target.is_(None), Product.gender_target != opposite))
    if scenario in FORMAL_SCENARIOS:
      query = query.where(Product.source.notin_(["sportmaster", "demix"]))
    if slot == "shoes" and scenario in FORMAL_SCENARIOS:
      query = query.where(or_(*(Product.title.ilike(f"%{term}%") for term in FORMAL_SHOE_TERMS)))
    for product in db.execute(query.limit(6000).execution_options(yield_per=300)).scalars():
      keys = product_identity_keys(product)
      if keys & seen_keys or product_slot(product) != slot:
        continue
      if anchor is not None and not pair_compatible(anchor, product):
        continue
      if not _scenario_product_ok(product, scenario) or not _hard_ok(product, fit=fit, rules_by_source=rules):
        continue
      out.append(product)
      seen_keys.update(keys)
      found += 1
      if found >= per_role:
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
  excluded_keys: set[str] | None = None,
) -> dict[str, list[SlotCandidate]]:
  slots = set().union(*[set(option) for option in SCENARIO_SLOT_OPTIONS[scenario]])
  buckets: dict[str, list[SlotCandidate]] = {slot: [] for slot in slots}
  seen: set[str] = set()
  seen_keys: set[str] = set(excluded_keys or ())
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
    keys = product_identity_keys(scored.product)
    if keys & seen_keys or not _hard_ok(scored.product, fit=fit, rules_by_source=rules_by_source):
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
    seen_keys.update(keys)

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
      excluded_keys=excluded_keys,
    )
    rows = [product for product in rows if _hard_ok(product, fit=fit, rules_by_source=rules_by_source)]
    ctx = build_feed_context(db, user, product_ids=[product.id for product in rows], scenario=scenario)
    for product in rows:
      keys = product_identity_keys(product)
      if keys & seen_keys:
        continue
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
      seen_keys.update(keys)

  for slot in buckets:
    buckets[slot].sort(key=lambda item: item.personal_score, reverse=True)
    buckets[slot] = buckets[slot][:per_slot]
  return buckets


def _colors(product: Product) -> set[str]:
  out = set(normalize_colors_value(product.colors or []))
  if product.color_family:
    out.update(normalize_colors_value(product.color_family))
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
  reasons = [f"{label}: одежда и обувь в одном образе"]
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
    slot_options = [buckets[slot][:10] for slot in required_slots]
    for combo in cartesian_product(*slot_options):
      slot_items = [item for item in combo if item is not None]
      if not outfit_compatible([item.product for item in slot_items]):
        continue
      colors = set().union(*(_colors(item.product) for item in slot_items))
      # A restrained base plus one accent is predictable for a first outfit.
      if len(colors - (NEUTRAL_COLORS | {"cream", "brown"})) > 1:
        continue
      seasons = {str(item.product.season or "").strip().lower() for item in slot_items}
      if seasons & {"winter", "зима"} and seasons & {"summer", "лето"}:
        continue
      texts = [garment_text(item.product) for item in slot_items]
      warm = any(any(word in text for word in ("утеплен", "мех", "теплоизоля", "шерстяной подклад")) for text in texts)
      light = any(any(word in text for word in ("льнян", "шифон", "сандал", "босонож", "linen", "chiffon")) for text in texts)
      if warm and light:
        continue
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
  candidates.sort(key=lambda item: item.final_score, reverse=True)
  return candidates


def _diverse_top(candidates: list[OutfitCandidate], *, limit: int) -> list[OutfitCandidate]:
  out: list[OutfitCandidate] = []
  selected_keys: list[set[str]] = []
  used_titles: set[tuple[str, str]] = set()
  for candidate in candidates:
    keys = set().union(*(product_identity_keys(item.product) for item in candidate.items.values()))
    # Do not reuse garments across newly generated outfits. A short list is
    # preferable to padding the requested count with near-identical looks.
    if any(keys & previous for previous in selected_keys):
      continue
    titles = {(str(item.product.brand or "").lower(), str(item.product.title or "").strip().lower()) for item in candidate.items.values() if len((item.product.title or "").split()) >= 4}
    if titles & used_titles:
      continue
    selected_keys.append(keys)
    used_titles.update(titles)
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
  # Serialize refreshes for this user; read history only after acquiring the lock.
  db.execute(select(User.id).where(User.id == user.id).with_for_update()).scalar_one()
  fit = db.execute(select(FitProfile).where(FitProfile.user_id == user.id)).scalar_one_or_none()
  excluded = _excluded_product_ids(db, user, scenario=scenario_key)
  skipped_ids = _recent_skip_product_ids(db, user)
  existing = db.execute(select(Outfit).where(Outfit.user_id == user.id)).scalars().all()
  prior_ids = {str(pid) for row in existing for pid in (row.items_json or {}).values() if pid}
  prior_products = {p.id: p for p in db.execute(select(Product).where(Product.id.in_(prior_ids))).scalars()} if prior_ids else {}
  recent_keys: list[set[str]] = []
  other_keys: list[set[str]] = []
  for row in existing:
    keys = set().union(*(product_identity_keys(prior_products[str(pid)]) for pid in (row.items_json or {}).values() if str(pid) in prior_products))
    (recent_keys if row.style_direction == scenario_key else other_keys).append(keys)

  # Keep five batches, including identity snapshots so deleted/aliased offers
  # cannot make the previous look appear new. No catalogue or schema mutation.
  history_name = f"outfit_rotation:{scenario_key}"
  history = db.execute(select(MetricEvent).where(
    MetricEvent.user_id == user.id, MetricEvent.name == history_name,
  ).order_by(MetricEvent.created_at.desc(), MetricEvent.id.desc()).limit(5)).scalars().all()
  for event in history:
    recent_keys.extend(set(keys) for keys in (event.meta_json or {}).get("outfits", []))
  used_keys = set().union(*recent_keys)
  used_ids = {key[3:] for key in used_keys if key.startswith("id:")}
  selected: list[OutfitCandidate] = []
  # Prefer unused garments. With scarce footwear, allow a shared item only
  # when at least two parts differ from every recent look in this scenario.
  for fresh_only in ([True, False] if recent_keys else [False]):
    buckets = _build_slot_candidates(
      db, user, scenario=scenario_key, fit=fit,
      excluded=excluded | used_ids if fresh_only else excluded,
      skipped_ids=skipped_ids, per_slot=10,
      excluded_keys=used_keys if fresh_only else None,
    )
    combinations = _build_combinations(buckets, scenario=scenario_key, fit=fit, limit=need)
    combinations = [candidate for candidate in combinations if all(
      sum(not bool(product_identity_keys(item.product) & keys) for item in candidate.items.values()) >= 2
      for keys in recent_keys
    ) and all(
      any(not (product_identity_keys(item.product) & keys) for item in candidate.items.values())
      for keys in other_keys
    )]
    selected = _diverse_top(combinations, limit=need)
    if selected:
      break
  if not selected:
    return []  # Preserve the current outfits when alternatives are exhausted.
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

  db.add(MetricEvent(
    id=str(uuid4()), user_id=user.id, name=history_name, created_at=now,
    meta_json={"outfits": [sorted(set().union(*(product_identity_keys(item.product) for item in candidate.items.values()))) for candidate in selected]},
  ))
  # The new batch plus the previous four are enough; bound storage per user.
  stale = history[4:]
  if stale:
    db.execute(delete(MetricEvent).where(MetricEvent.id.in_([event.id for event in stale])))
  db.flush()
  return created
