from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..catalog_normalize import normalize_category
from ..models import FitProfile, MetricEvent, Outfit, Product, RecommendationEventV2, StyleProfile, User
from ..schemas.photo_analysis import extract_analysis_section
from .catalog.catalog_quality import product_is_feed_eligible
from .catalog.rule_filters import load_rules_by_source_id, product_gender_compatible, product_passes_source_rules
from .recommendation_config import OUTFIT_ENGINE_VERSION
from .recommendation_engine import ensure_style_profile

logger = logging.getLogger(__name__)

_TOP_CATS = frozenset({"футболки", "рубашки", "верхний_слой"})
_BOTTOM_CATS = frozenset({"джинсы", "брюки"})
_SHOES_CATS = frozenset({"обувь"})
_ACCESSORY_CATS = frozenset({"аксессуары", "сумки"})

_SLOT_CATEGORIES: dict[str, list[str]] = {
  "top": ["футболки", "рубашки", "верхний_слой"],
  "bottom": ["джинсы", "брюки"],
  "shoes": ["обувь"],
  "accessory": ["аксессуары", "сумки"],
}

_SCENARIO_LABELS = {
  "daily": "Каждый день",
  "office": "В офис",
  "evening": "Вечер",
  "casual": "Casual",
  "minimal": "Минимализм",
}

_SCENARIO_TOP_PREF: dict[str, list[str]] = {
  "daily": ["футболки", "рубашки", "верхний_слой"],
  "office": ["рубашки", "верхний_слой", "футболки"],
  "evening": ["верхний_слой", "рубашки"],
  "casual": ["футболки", "рубашки"],
  "minimal": ["рубашки", "футболки"],
}

_SCENARIO_BOTTOM_PREF: dict[str, list[str]] = {
  "daily": ["джинсы", "брюки"],
  "office": ["брюки", "джинсы"],
  "evening": ["брюки", "джинсы"],
  "casual": ["джинсы", "брюки"],
  "minimal": ["брюки", "джинсы"],
}

_SCENARIO_SHOES_PREF: dict[str, list[str]] = {
  "daily": ["обувь"],
  "office": ["обувь"],
  "evening": ["обувь"],
  "casual": ["обувь"],
  "minimal": ["обувь"],
}

_KIDS_TITLE_MARKERS = ("детск", "для детей", "kids", "kid ", "junior", "малыш", "мальчик", "девочк")
_OFFICE_AVOID_IN_TITLE = ("бокс", "boxing", "everlast", "борц", "штанга", "фитнес-перчат")
_LEGACY_OUTFIT_ENGINE_VERSION = "legacy_outfit_service"
_SKIP_PENALTY_COOLDOWN_HOURS = 24


def _norm_list(values: Any) -> list[str]:
  if not isinstance(values, list):
    return []
  out: list[str] = []
  for x in values:
    s = str(x).strip().lower()
    if s and s not in out:
      out.append(s)
  return out


def _product_slot(product: Product) -> str | None:
  raw = (product.category_name or product.category or "").strip()
  cat = normalize_category(raw)
  if not cat:
    low = raw.lower()
    if any(k in low for k in ("сумк", "bag", "аксессуар", "accessory")):
      return "accessory"
    return None
  if cat in _TOP_CATS:
    return "top"
  if cat in _BOTTOM_CATS:
    return "bottom"
  if cat in _SHOES_CATS:
    return "shoes"
  if cat in _ACCESSORY_CATS:
    return "accessory"
  if any(k in cat for k in ("сумк", "аксессуар")):
    return "accessory"
  return None


def _dedupe_products(products: list[Product]) -> list[Product]:
  seen: set[str] = set()
  out: list[Product] = []
  for p in products:
    if p.id in seen:
      continue
    seen.add(p.id)
    out.append(p)
  return out


def _scenario_rng(scenario: str) -> random.Random:
  return random.Random(hash(f"modish-outfit:{scenario}") & 0xFFFFFFFF)


def _scenario_product_ok(product: Product, scenario: str, budget_max: int | None) -> bool:
  title = (product.title or "").lower()
  if any(m in title for m in _KIDS_TITLE_MARKERS):
    return False
  if scenario in ("office", "evening") and any(m in title for m in _OFFICE_AVOID_IN_TITLE):
    return False
  price = int(product.price or 0)
  if budget_max and budget_max > 0 and price > budget_max:
    return False
  if price > 500_000:
    return False
  return True


def _sort_bucket(pool: list[Product], pref: list[str]) -> list[Product]:
  return sorted(
    pool,
    key=lambda p: (
      pref.index(normalize_category(p.category_name or p.category))
      if normalize_category(p.category_name or p.category) in pref
      else 99,
      int(p.price or 0),
    ),
  )


def _recent_skip_product_ids(db: Session, user: User) -> set[str]:
  cutoff = datetime.now(timezone.utc) - timedelta(hours=_SKIP_PENALTY_COOLDOWN_HOURS)
  ids = db.execute(
    select(RecommendationEventV2.product_id).where(
      RecommendationEventV2.user_id == user.id,
      RecommendationEventV2.product_id.is_not(None),
      RecommendationEventV2.event_type == "skip",
      RecommendationEventV2.created_at >= cutoff,
    )
  ).scalars()
  return {str(pid) for pid in ids if str(pid).strip()}


def _bucket_products(
  products: list[Product],
  scenario: str,
  *,
  budget_max: int | None = None,
  skipped_ids: set[str] | None = None,
) -> dict[str, list[Product]]:
  buckets: dict[str, list[Product]] = {
    "top": [],
    "bottom": [],
    "shoes": [],
    "accessory": [],
  }
  top_pref = _SCENARIO_TOP_PREF.get(scenario, _SCENARIO_TOP_PREF["daily"])
  bottom_pref = _SCENARIO_BOTTOM_PREF.get(scenario, _SCENARIO_BOTTOM_PREF["daily"])
  for p in products:
    if not _scenario_product_ok(p, scenario, budget_max):
      continue
    slot = _product_slot(p)
    if slot:
      buckets[slot].append(p)

  for slot in buckets:
    buckets[slot] = _dedupe_products(buckets[slot])

  buckets["top"] = _sort_bucket(buckets["top"], top_pref)
  buckets["bottom"] = _sort_bucket(buckets["bottom"], bottom_pref)
  rng = _scenario_rng(scenario)
  skipped_ids = skipped_ids or set()
  for slot in buckets:
    rng.shuffle(buckets[slot])
    if skipped_ids:
      buckets[slot].sort(key=lambda p: p.id in skipped_ids)
  return buckets


def _product_ids_in_other_outfits(db: Session, user_id: str, *, scenario: str) -> set[str]:
  """Товары из образов других сценариев — не повторять между «Офис» / «Вечер» / «Каждый день»."""
  rows = db.execute(
    select(Outfit).where(Outfit.user_id == user_id, Outfit.is_saved == 0)
  ).scalars().all()
  ids: set[str] = set()
  for o in rows:
    if (o.style_direction or "").strip().lower() == scenario:
      continue
    for pid in (o.items_json or {}).values():
      if pid:
        ids.add(str(pid))
  return ids


def _extend_buckets(
  db: Session,
  buckets: dict[str, list[Product]],
  *,
  excluded: set[str],
  min_per_slot: int,
  scenario: str = "daily",
  fit: FitProfile | None = None,
  budget_max: int | None = None,
) -> None:
  """Добираем товары из каталога, если в ленте мало позиций для разнообразия."""
  rules = load_rules_by_source_id(db)
  for slot, cats in _SLOT_CATEGORIES.items():
    if len(buckets[slot]) >= min_per_slot:
      continue
    existing = {p.id for p in buckets[slot]}
    rows = db.execute(
      select(Product)
      .where(
        Product.is_active == 1,
        Product.is_available == 1,
        Product.is_deleted_from_feed == 0,
        Product.source != "demo",
        Product.category.in_(cats),
      )
      .order_by(Product.created_at.desc())
      .limit(120)
    ).scalars().all()
    for p in rows:
      if p.id in excluded or p.id in existing:
        continue
      if not product_is_feed_eligible(p):
        continue
      if not product_passes_source_rules(p, rules):
        continue
      if fit and not product_gender_compatible(p, fit.gender_target):
        continue
      if not _scenario_product_ok(p, scenario, budget_max):
        continue
      buckets[slot].append(p)
      existing.add(p.id)
      if len(buckets[slot]) >= min_per_slot:
        break


def _pick_unique(
  slot: str,
  pool: list[Product],
  *,
  cursors: dict[str, int],
  used_in_batch: set[str],
) -> Product | None:
  if not pool:
    return None
  n = len(pool)
  start = cursors[slot]
  for i in range(n):
    p = pool[(start + i) % n]
    if p.id not in used_in_batch:
      cursors[slot] = (start + i + 1) % n
      used_in_batch.add(p.id)
      return p
  p = pool[start % n]
  cursors[slot] = (start + 1) % n
  return p


def _catalog_products_for_outfits(
  db: Session,
  user: User,
  *,
  excluded: set[str],
  limit: int = 200,
) -> list[Product]:
  """Быстрый набор товаров для образов — без тяжёлого скоринга всей ленты."""
  rows = db.execute(
    select(Product)
    .where(
      Product.is_active == 1,
      Product.is_available == 1,
      Product.is_deleted_from_feed == 0,
      Product.source != "demo",
    )
    .order_by(Product.created_at.desc())
    .limit(600)
  ).scalars().all()
  fit = db.execute(select(FitProfile).where(FitProfile.user_id == user.id)).scalar_one_or_none()
  rules = load_rules_by_source_id(db)
  out: list[Product] = []
  for p in rows:
    if p.id in excluded:
      continue
    if not product_is_feed_eligible(p):
      continue
    if not product_passes_source_rules(p, rules):
      continue
    if fit and not product_gender_compatible(p, fit.gender_target):
      continue
    out.append(p)
    if len(out) >= limit:
      break
  return out


def _generate_outfits_fallback(
  db: Session,
  user: User,
  *,
  count: int = 3,
  scenario: str = "daily",
  replace_existing: bool = True,
) -> list[Outfit]:
  """Собирает образы из каталога; в одной пачке старается не повторять товары."""
  scenario_key = scenario.strip().lower() if scenario else "daily"
  if scenario_key not in _SCENARIO_LABELS:
    scenario_key = "daily"

  profile = ensure_style_profile(db, user.id)
  analysis = extract_analysis_section(profile.profile_json or {})
  palette = _norm_list(analysis.get("color_palette"))[:4]

  fit = db.execute(select(FitProfile).where(FitProfile.user_id == user.id)).scalar_one_or_none()
  if fit and fit.style_scenarios:
    preferred = [str(x).strip().lower() for x in fit.style_scenarios if str(x).strip()]
    if scenario_key == "daily" and preferred:
      scenario_key = preferred[0] if preferred[0] in _SCENARIO_LABELS else scenario_key

  excluded = set(
    db.execute(
      select(RecommendationEventV2.product_id).where(
        RecommendationEventV2.user_id == user.id,
        RecommendationEventV2.product_id.is_not(None),
        RecommendationEventV2.event_type.in_(["dislike", "post_purchase_negative"]),
      )
    ).scalars()
  )
  excluded = {str(x) for x in excluded if x}
  excluded |= _product_ids_in_other_outfits(db, user.id, scenario=scenario_key)
  skipped_ids = _recent_skip_product_ids(db, user)

  budget_max = int(fit.budget_max) if fit and fit.budget_max else None

  if replace_existing:
    db.execute(
      delete(Outfit).where(
        Outfit.user_id == user.id,
        Outfit.style_direction == scenario_key,
        Outfit.is_saved == 0,
      )
    )
    db.flush()

  need = max(1, min(10, int(count)))
  products = _catalog_products_for_outfits(db, user, excluded=excluded, limit=280)
  rng = _scenario_rng(scenario_key)
  if len(products) > 40:
    pivot = rng.randint(0, len(products) - 1)
    products = products[pivot:] + products[:pivot]
  buckets = _bucket_products(products, scenario_key, budget_max=budget_max, skipped_ids=skipped_ids)
  _extend_buckets(
    db,
    buckets,
    excluded=excluded,
    min_per_slot=need + 4,
    scenario=scenario_key,
    fit=fit,
    budget_max=budget_max,
  )

  if not any(buckets[s] for s in ("top", "bottom", "shoes")):
    fallback = db.execute(
      select(Product).where(
        Product.is_active == 1,
        Product.is_deleted_from_feed == 0,
        Product.source != "demo",
      ).limit(300)
    ).scalars().all()
    fallback = [p for p in fallback if p.id not in excluded]
    buckets = _bucket_products(fallback, scenario_key, budget_max=budget_max, skipped_ids=skipped_ids)
    _extend_buckets(
      db,
      buckets,
      excluded=excluded,
      min_per_slot=need + 4,
      scenario=scenario_key,
      fit=fit,
      budget_max=budget_max,
    )

  label = _SCENARIO_LABELS[scenario_key]
  created: list[Outfit] = []
  now = datetime.now(timezone.utc)
  used_in_batch: set[str] = set()
  cursors = {
    "top": rng.randint(0, max(0, len(buckets["top"]) - 1)) if buckets["top"] else 0,
    "bottom": rng.randint(0, max(0, len(buckets["bottom"]) - 1)) if buckets["bottom"] else 0,
    "shoes": rng.randint(0, max(0, len(buckets["shoes"]) - 1)) if buckets["shoes"] else 0,
    "accessory": rng.randint(0, max(0, len(buckets["accessory"]) - 1)) if buckets["accessory"] else 0,
  }
  seen_combos: set[tuple[str, ...]] = set()
  attempts = 0
  max_attempts = need * 40

  while len(created) < need and attempts < max_attempts:
    attempts += 1
    top = _pick_unique("top", buckets["top"], cursors=cursors, used_in_batch=used_in_batch)
    bottom = _pick_unique("bottom", buckets["bottom"], cursors=cursors, used_in_batch=used_in_batch)
    shoes = _pick_unique("shoes", buckets["shoes"], cursors=cursors, used_in_batch=used_in_batch)
    accessory = _pick_unique("accessory", buckets["accessory"], cursors=cursors, used_in_batch=used_in_batch)

    items: dict[str, Any] = {}
    total = 0
    ids: list[str] = []
    for slot, prod in (
      ("top", top),
      ("bottom", bottom),
      ("shoes", shoes),
      ("accessory", accessory),
    ):
      if prod is None:
        continue
      items[slot] = prod.id
      ids.append(prod.id)
      total += int(prod.price or 0)

    if len(items) < 2:
      continue

    combo_key = tuple(sorted(ids))
    if combo_key in seen_combos:
      continue
    seen_combos.add(combo_key)

    reason_bits = [f"Сценарий: {label}"]
    if palette:
      reason_bits.append(f"Палитра: {', '.join(palette[:3])}")
    reason_bits.append(f"engine: {_LEGACY_OUTFIT_ENGINE_VERSION}")

    out = Outfit(
      id=str(uuid4()),
      user_id=user.id,
      items_json=items,
      total_price=total,
      style_direction=scenario_key,
      reason=". ".join(reason_bits) + ".",
      score=0.72,
      is_saved=0,
      created_at=now,
      updated_at=now,
    )
    db.add(out)
    created.append(out)

  db.flush()
  return created


def _record_outfit_engine_v2_failure(db: Session, user: User, *, scenario: str, exc: Exception) -> None:
  logger.exception("Outfit Engine v2 failed; falling back to legacy outfit service")
  try:
    db.add(
      MetricEvent(
        id=str(uuid4()),
        user_id=user.id,
        name="outfit_engine_v2_failure",
        meta_json={
          "engine": OUTFIT_ENGINE_VERSION,
          "fallback_engine": _LEGACY_OUTFIT_ENGINE_VERSION,
          "scenario": (scenario or "daily").strip().lower() or "daily",
          "error_type": exc.__class__.__name__,
          "error": str(exc)[:500],
        },
        created_at=datetime.now(timezone.utc),
      )
    )
    db.flush()
  except Exception:
    logger.exception("Failed to record outfit_engine_v2_failure metric")


def generate_outfits(
  db: Session,
  user: User,
  *,
  count: int = 3,
  scenario: str = "daily",
  replace_existing: bool = True,
  use_v2: bool = True,
) -> list[Outfit]:
  if use_v2:
    try:
      from .outfit_engine_v2 import generate_outfits_v2

      created = generate_outfits_v2(
        db,
        user,
        count=count,
        scenario=scenario,
        replace_existing=replace_existing,
      )
      if created:
        return created
    except Exception as exc:
      _record_outfit_engine_v2_failure(db, user, scenario=scenario, exc=exc)

  return _generate_outfits_fallback(
    db,
    user,
    count=count,
    scenario=scenario,
    replace_existing=replace_existing,
  )
