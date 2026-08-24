"""Rule-based product scoring for personalized feed (v3).

Формула по спецификации MIE (гл. 31.14–31.15, 33.26):
  Final Score = Style + Category + Color + Budget + Popularity
  + жёсткие фильтры (пол/размер/бюджет) до скоринга
  + Diversity Layer (гл. 33.32): не более 3 подряд одной категории/бренда
  + Explainability (гл. 33.33): причины для каждого товара.

Отличия v3 от v2:
  * пул кандидатов выбирается стабильным пригодным срезом каталога,
    а не случайной лотереей перед персональным скорингом;
  * цвета товара и палитра пользователя приводятся к одному словарю
    канонических цветов (русский/английский/свободный текст);
  * добавлен компонент Popularity (лайки/сохранения/переходы всех
    пользователей за 60 дней);
  * убраны запросы к БД на каждый товар (batch-префетч) — лента
    считается на порядок быстрее;
  * добавлен Diversity Layer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from ..catalog_normalize import (
  canon_colors_from_text,
  normalize_category,
  normalize_product_colors,
  product_gender_from_model,
)
from ..models import FitProfile, Product, RecommendationEventV2, StyleProfile, TasteProfile, User
from .catalog.rule_filters import load_rules_by_source_id, product_gender_compatible, product_passes_source_rules
from .catalog.catalog_quality import product_is_feed_eligible
from .feed_filters import product_passes_hard_filters
from ..schemas.photo_analysis import extract_analysis_section


@dataclass(frozen=True)
class ScoredProduct:
  product: Product
  final_score: float
  breakdown: dict[str, float]
  reason: str
  reasons: list[str]


@dataclass
class FeedContext:
  """Всё, что нужно для скоринга, предзагруженное одним махом."""
  taste: TasteProfile
  fit: FitProfile | None
  palette: set[str] = field(default_factory=set)
  avoid_colors: set[str] = field(default_factory=set)
  saved_categories: set[str] = field(default_factory=set)
  seen_counts: dict[str, int] = field(default_factory=dict)
  popularity: dict[str, float] = field(default_factory=dict)


def _norm_list(values: Any) -> list[str]:
  if not isinstance(values, list):
    return []
  out: list[str] = []
  for x in values:
    s = str(x).strip().lower()
    if s and s not in out:
      out.append(s)
  return out


def ensure_style_profile(db: Session, user_id: str) -> StyleProfile:
  row = db.execute(select(StyleProfile).where(StyleProfile.user_id == user_id)).scalar_one_or_none()
  if row:
    return row
  now = datetime.now(timezone.utc)
  row = StyleProfile(
    id=str(uuid4()),
    user_id=user_id,
    style_target="unknown",
    confidence_score=0.5,
    profile_json={},
    created_at=now,
    updated_at=now,
  )
  db.add(row)
  db.flush()
  return row


def ensure_taste_profile(db: Session, user_id: str) -> TasteProfile:
  row = db.execute(select(TasteProfile).where(TasteProfile.user_id == user_id)).scalar_one_or_none()
  if row:
    return row
  now = datetime.now(timezone.utc)
  row = TasteProfile(
    id=str(uuid4()),
    user_id=user_id,
    liked_categories=[],
    disliked_categories=[],
    liked_colors=[],
    disliked_colors=[],
    liked_brands=[],
    disliked_brands=[],
    liked_styles=[],
    disliked_styles=[],
    category_weights={},
    brand_weights={},
    color_weights={},
    style_weights={},
    price_min=0,
    price_max=10_000,
    preferred_fit="regular",
    profile_version=1,
    created_at=now,
    updated_at=now,
  )
  db.add(row)
  db.flush()
  return row


def _product_category_norms(product: Product) -> set[str]:
  keys: set[str] = set()
  for raw in (product.category_name, product.category):
    if raw and str(raw).strip():
      k = normalize_category(str(raw).strip())
      if k:
        keys.add(k)
  return keys


def expand_product_exclusions(db: Session, product_ids: set[str] | list[str] | None) -> set[str]:
  """Expand hidden SKU ids to all variants of the same source/group_id item."""
  ids = {str(pid).strip() for pid in (product_ids or set()) if str(pid).strip()}
  if not ids:
    return set()

  rows = db.execute(
    select(Product.source, Product.group_id).where(
      Product.id.in_(ids),
      Product.group_id.is_not(None),
      Product.group_id != "",
    )
  ).all()
  groups_by_source: dict[str, set[str]] = {}
  for source, group_id in rows:
    src = str(source or "").strip()
    gid = str(group_id or "").strip()
    if src and gid:
      groups_by_source.setdefault(src, set()).add(gid)

  if not groups_by_source:
    return ids

  group_conds = [
    and_(Product.source == source, Product.group_id.in_(groups))
    for source, groups in groups_by_source.items()
    if groups
  ]
  if not group_conds:
    return ids

  sibling_ids = db.execute(select(Product.id).where(or_(*group_conds))).scalars().all()
  return ids | {str(pid).strip() for pid in sibling_ids if str(pid).strip()}


def _weight(d: Any, key: str) -> float:
  if not isinstance(d, dict):
    return 0.0
  try:
    return float(d.get(key, 0.0))
  except Exception:
    return 0.0


def _product_canon_colors(product: Product) -> set[str]:
  """Цвета товара: канонические + исходные строки (для старых весов вкуса)."""
  raw = _norm_list(product.colors)
  canon = normalize_product_colors(list(product.colors or []))
  if not canon and getattr(product, "color_original", None):
    canon = normalize_product_colors([product.color_original])
  return set(raw) | set(canon)


def _batch_seen_counts(db: Session, *, user_id: str, product_ids: list[str]) -> dict[str, int]:
  """view/open_product за 30 дней по всем кандидатам одним запросом."""
  if not product_ids:
    return {}
  since = datetime.now(timezone.utc) - timedelta(days=30)
  rows = db.execute(
    select(RecommendationEventV2.product_id, func.count())
    .where(
      RecommendationEventV2.user_id == user_id,
      RecommendationEventV2.product_id.in_(product_ids),
      RecommendationEventV2.event_type.in_(["view", "open_product"]),
      RecommendationEventV2.created_at >= since,
    )
    .group_by(RecommendationEventV2.product_id)
  ).all()
  return {str(pid): int(n or 0) for pid, n in rows}


def _batch_saved_categories(db: Session, *, user_id: str) -> set[str]:
  """Категории сохранённых товаров за 90 дней — один запрос на ленту."""
  since = datetime.now(timezone.utc) - timedelta(days=90)
  rows = db.execute(
    select(Product.category, Product.category_name)
    .join(RecommendationEventV2, RecommendationEventV2.product_id == Product.id)
    .where(
      RecommendationEventV2.user_id == user_id,
      RecommendationEventV2.event_type == "save",
      RecommendationEventV2.created_at >= since,
    )
    .limit(200)
  ).all()
  saved: set[str] = set()
  for cat, cat_name in rows:
    for raw in (cat_name, cat):
      if raw and str(raw).strip():
        k = normalize_category(str(raw).strip())
        if k:
          saved.add(k)
  return saved


# Позитивные события для Popularity (гл. 33.31)
_POPULARITY_EVENTS = (
  "like",
  "save",
  "affiliate_click",
  "purchase",
  "post_purchase_positive",
  "buy_click",  # legacy events created before Phase 1
)


def _batch_popularity(db: Session, *, product_ids: list[str]) -> dict[str, float]:
  """Популярность 0..10 по позитивным событиям всех пользователей за 60 дней."""
  if not product_ids:
    return {}
  since = datetime.now(timezone.utc) - timedelta(days=60)
  rows = db.execute(
    select(RecommendationEventV2.product_id, func.count())
    .where(
      RecommendationEventV2.product_id.in_(product_ids),
      RecommendationEventV2.event_type.in_(list(_POPULARITY_EVENTS)),
      RecommendationEventV2.created_at >= since,
    )
    .group_by(RecommendationEventV2.product_id)
  ).all()
  counts = {str(pid): int(n or 0) for pid, n in rows}
  if not counts:
    return {}
  top = max(counts.values())
  if top <= 0:
    return {}
  return {pid: 10.0 * n / top for pid, n in counts.items()}


def build_feed_context(db: Session, user: User, *, product_ids: list[str] | None = None) -> FeedContext:
  profile = ensure_style_profile(db, user.id)
  analysis = extract_analysis_section(profile.profile_json or {})
  taste = ensure_taste_profile(db, user.id)
  fit = db.execute(select(FitProfile).where(FitProfile.user_id == user.id)).scalar_one_or_none()

  # палитра из фото-анализа — свободный текст → канонические цвета
  palette = set(canon_colors_from_text(analysis.get("color_palette")))
  avoid = set(canon_colors_from_text(analysis.get("avoid_colors")))
  if fit is not None:
    palette |= set(canon_colors_from_text(getattr(fit, "color_palette", None)))
    avoid |= set(canon_colors_from_text(getattr(fit, "avoid_colors", None)))

  pids = list(product_ids or [])
  return FeedContext(
    taste=taste,
    fit=fit,
    palette=palette,
    avoid_colors=avoid,
    saved_categories=_batch_saved_categories(db, user_id=user.id),
    seen_counts=_batch_seen_counts(db, user_id=user.id, product_ids=pids),
    popularity=_batch_popularity(db, product_ids=pids),
  )


def score_product(db: Session, user: User, product: Product, ctx: FeedContext | None = None) -> ScoredProduct:
  """
  Rule-based score (v3):
  gender + category + size + color + budget + style + brand
  + saved_category_boost + popularity
  - disliked_* penalties - already_seen_penalty
  """
  if ctx is None:
    ctx = build_feed_context(db, user, product_ids=[product.id])

  taste = ctx.taste
  fit = ctx.fit
  palette = ctx.palette
  avoid_colors = ctx.avoid_colors

  cat_keys = _product_category_norms(product)
  p_colors = _product_canon_colors(product)
  brand = (product.brand or "").strip().lower()
  style_tags = set(_norm_list(product.style_tags))
  reasons: list[str] = []
  bd: dict[str, float] = {}

  score = 0.0

  # gender_match — приоритет для персонализации
  gender_match = 0.0
  if fit and (fit.gender_target or "").strip().lower() in ("menswear", "womenswear"):
    ug = fit.gender_target.strip().lower()
    pg = product_gender_from_model(product)
    if pg == ug:
      gender_match = 18.0
      reasons.append("Под ваш профиль")
    elif pg == "unisex":
      gender_match = 6.0
    elif pg and pg != ug:
      gender_match = -80.0
  score += gender_match
  bd["gender_match"] = gender_match

  # category_match
  cat_match = 0.0
  liked_norm = {normalize_category(str(x).strip()) for x in (taste.liked_categories or []) if str(x).strip()}
  disliked_norm = {normalize_category(str(x).strip()) for x in (taste.disliked_categories or []) if str(x).strip()}
  if taste.category_weights and cat_keys:
    cat_match = float(max((_weight(taste.category_weights, ck) for ck in cat_keys), default=0))
    if cat_match >= 6:
      reasons.append("Категория вам подходит")
  elif cat_keys & liked_norm:
    cat_match = 15.0
    reasons.append("Вы часто выбираете эту категорию")
  if fit and fit.interest_categories and cat_keys:
    interest = {normalize_category(str(x).strip()) for x in fit.interest_categories if str(x).strip()}
    if cat_keys & interest:
      cat_match = max(cat_match, 12.0)
      reasons.append("Совпадает с вашими интересами")
  score += cat_match
  bd["category_match"] = cat_match

  # disliked_category_penalty
  disliked_cat_pen = 0.0
  if cat_keys & disliked_norm:
    disliked_cat_pen = 20.0
    reasons.append("Категория вам не нравится")
  elif taste.category_weights and cat_keys:
    neg = min((_weight(taste.category_weights, ck) for ck in cat_keys), default=0)
    if neg < -2:
      disliked_cat_pen = min(20.0, float(-neg))
  score -= disliked_cat_pen
  bd["disliked_category_penalty"] = disliked_cat_pen

  # size_match
  size_match = 0.0
  if product.available_sizes:
    if fit and (fit.clothing_size or "").strip():
      size = fit.clothing_size.strip().upper()
      sizes = {str(x).strip().upper() for x in product.available_sizes if str(x).strip()}
      if size in sizes:
        size_match = 12.0
        reasons.append("Есть ваш размер")
    else:
      size_match = 6.0
  score += size_match
  bd["size_match"] = size_match

  # color_match
  color_match = 0.0
  if palette and (palette & p_colors):
    color_match += 12.0
    reasons.append("Цвет из вашей палитры")
  if taste.color_weights:
    for c in p_colors:
      w = _weight(taste.color_weights, c)
      if w > 0:
        color_match += min(8.0, float(w))
      if w >= 6:
        reasons.append("Похожий цвет вам нравится")
  if p_colors and (set(_norm_list(taste.liked_colors)) & p_colors):
    color_match = max(color_match, 10.0)
  score += color_match
  bd["color_match"] = color_match

  # disliked_color_penalty
  disliked_color_pen = 0.0
  if avoid_colors and (avoid_colors & p_colors):
    disliked_color_pen += 12.0
    reasons.append("Цвет лучше избегать")
  if p_colors and (set(_norm_list(taste.disliked_colors)) & p_colors):
    disliked_color_pen = max(disliked_color_pen, 15.0)
  elif taste.color_weights:
    for c in p_colors:
      w = _weight(taste.color_weights, c)
      if w < -2:
        disliked_color_pen = max(disliked_color_pen, min(15.0, float(-w)))
  score -= disliked_color_pen
  bd["disliked_color_penalty"] = disliked_color_pen

  # budget_match
  lo = int(taste.price_min or 0)
  hi = int(taste.price_max or 200_000)
  if fit is not None:
    bmin = int(fit.budget_min or 0)
    bmax = int(fit.budget_max or 0)
    if bmin > 0:
      lo = max(lo, bmin)
    if bmax > 0:
      hi = min(hi, bmax)
  if hi < lo:
    lo, hi = hi, lo
  if hi == lo:
    hi = lo + 1
  pr = int(product.price or 0)
  budget_match = 0.0
  if lo <= pr <= hi:
    budget_match = 10.0
    reasons.append("Цена в рамках бюджета")
  elif pr <= hi * 1.15:
    budget_match = 4.0
  else:
    budget_match = -6.0
    reasons.append("Выше бюджета")
  score += budget_match
  bd["budget_match"] = budget_match
  bd["budget_lo"] = float(lo)
  bd["budget_hi"] = float(hi)

  # style_match — главный компонент персонализации (гл. 31.15: 40%)
  style_match = 0.0
  fit_styles = set(_norm_list(fit.style_scenarios if fit else []))
  if fit_styles and style_tags and (fit_styles & style_tags):
    style_match += 10.0
    reasons.append("Подходит под ваш сценарий")
  if taste.style_weights and style_tags:
    w_sum = 0.0
    for t in style_tags:
      w = _weight(taste.style_weights, t)
      if w > 0:
        w_sum += min(6.0, float(w))
    if w_sum > 0:
      style_match += min(12.0, w_sum)
      if w_sum >= 6:
        reasons.append("Похоже на то, что вам нравится")
  if style_tags and (set(_norm_list(taste.liked_styles)) & style_tags):
    style_match = max(style_match, 10.0)
    reasons.append("Подходит вашему стилю")
  score += style_match
  bd["style_match"] = style_match

  # liked_brand_boost
  brand_boost = 0.0
  if taste.brand_weights and brand:
    brand_boost = float(_weight(taste.brand_weights, brand))
    if brand_boost >= 6:
      reasons.append("Бренд вам нравится")
  elif brand and brand in set(_norm_list(taste.liked_brands)):
    brand_boost = 10.0
    reasons.append("Любимый бренд")
  if brand and brand in set(_norm_list(taste.disliked_brands)):
    brand_boost -= 12.0
  score += brand_boost
  bd["liked_brand_boost"] = brand_boost

  # saved_category_boost
  saved_boost = 8.0 if (ctx.saved_categories and (ctx.saved_categories & cat_keys)) else 0.0
  if saved_boost > 0:
    reasons.append("Похоже на сохранённые вещи")
  score += saved_boost
  bd["saved_category_boost"] = saved_boost

  # popularity (гл. 33.31)
  pop = float(ctx.popularity.get(product.id, 0.0))
  if pop >= 6.0:
    reasons.append("Популярно у пользователей")
  score += pop
  bd["popularity"] = pop

  # already_seen_penalty
  eng_n = int(ctx.seen_counts.get(product.id, 0))
  seen_pen = min(15.0, float(eng_n) * 5.0)
  if seen_pen > 0:
    reasons.append("Вы уже смотрели этот товар")
  score -= seen_pen
  bd["already_seen_penalty"] = seen_pen
  bd["engagement_recent_views"] = float(eng_n)

  final = max(0.0, score)
  bd["raw_score"] = final

  uniq: list[str] = []
  seen: set[str] = set()
  for r in reasons:
    if r not in seen:
      seen.add(r)
      uniq.append(r)
  reason = uniq[0] if uniq else "Подобрано под ваш профиль"
  return ScoredProduct(
    product=product,
    final_score=final,
    breakdown=bd,
    reason=reason,
    reasons=uniq[:8],
  )


def _apply_diversity(scored: list[ScoredProduct], *, max_run: int = 3) -> list[ScoredProduct]:
  """Diversity Layer (гл. 33.32): не более max_run подряд одной категории
  и одного бренда. Жадная перестановка без выбрасывания товаров."""
  if len(scored) <= max_run:
    return scored
  remaining = list(scored)
  out: list[ScoredProduct] = []

  def key_cat(s: ScoredProduct) -> str:
    return normalize_category(str(s.product.category or "")) or "?"

  def key_brand(s: ScoredProduct) -> str:
    return (s.product.brand or "").strip().lower() or "?"

  def violates(cand: ScoredProduct, *, check_brand: bool = True) -> bool:
    if len(out) < max_run:
      return False
    tail = out[-max_run:]
    if all(key_cat(t) == key_cat(cand) for t in tail):
      return True
    if check_brand and all(key_brand(t) == key_brand(cand) for t in tail):
      return True
    return False

  while remaining:
    placed = False
    # 1) идеальный кандидат: не нарушает ни категорию, ни бренд
    for i, cand in enumerate(remaining):
      if not violates(cand):
        out.append(remaining.pop(i))
        placed = True
        break
    if placed:
      continue
    # 2) каталог одного бренда: ослабляем правило бренда, категорию держим
    for i, cand in enumerate(remaining):
      if not violates(cand, check_brand=False):
        out.append(remaining.pop(i))
        placed = True
        break
    if placed:
      continue
    # 3) остались только товары одной категории — берём лучший по скору
    out.append(remaining.pop(0))
  return out


def generate_feed(
  db: Session,
  user: User,
  *,
  limit: int = 30,
  exclude_product_ids: set[str] | None = None,
  source: str | None = None,
  max_to_score: int = 400,
) -> list[ScoredProduct]:
  exclude_product_ids = expand_product_exclusions(db, exclude_product_ids)
  min_price = 500
  conds = [
    Product.is_active == 1,
    Product.is_available == 1,
    Product.is_deleted_from_feed == 0,
    Product.source != "demo",
    Product.price > min_price,
    Product.image_url.isnot(None),
    Product.image_url != "",
    or_(
      and_(Product.affiliate_url.isnot(None), Product.affiliate_url != ""),
      Product.product_url != "",
    ),
  ]
  if source and source.strip():
    conds.append(Product.source == source.strip())
  if exclude_product_ids:
    conds.append(Product.id.notin_(list(exclude_product_ids)))
  q = (
    select(Product)
    .where(and_(*conds))
    .order_by(Product.updated_at.desc(), Product.created_at.desc(), Product.id.asc())
    .limit(1000)
  )
  products = db.execute(q).scalars().all()
  if exclude_product_ids:
    products = [p for p in products if p.id not in exclude_product_ids]
  rules_by_source = load_rules_by_source_id(db)
  fit = db.execute(select(FitProfile).where(FitProfile.user_id == user.id)).scalar_one_or_none()
  # Бельё не попадает в общую ленту стилиста (если явно не выбрано в интересах)
  interest_norm = {
    normalize_category(str(x).strip())
    for x in ((fit.interest_categories or []) if fit else [])
    if str(x).strip()
  }

  def _is_hidden_category(p: Product) -> bool:
    cats = _product_category_norms(p)
    hidden = {"бельё"} - interest_norm
    return bool(cats and cats <= hidden)

  filtered: list[Product] = []
  for p in products:
    if not product_is_feed_eligible(p):
      continue
    if _is_hidden_category(p):
      continue
    if not product_passes_source_rules(p, rules_by_source):
      continue
    if not product_passes_hard_filters(p, fit):
      continue
    filtered.append(p)

  # Если выбраны категории в профиле и лента пустая — ослабляем только фильтр категорий.
  if not filtered and fit and fit.interest_categories:
    from .feed_filters import product_passes_budget, product_passes_size

    for p in products:
      if not product_is_feed_eligible(p):
        continue
      if not product_passes_source_rules(p, rules_by_source):
        continue
      if not product_gender_compatible(p, fit.gender_target if fit else None):
        continue
      if not product_passes_budget(p, fit):
        continue
      if not product_passes_size(p, fit):
        continue
      filtered.append(p)

  # Всё ещё пусто — ослабляем бюджет/размер, пол и категории не трогаем.
  if not filtered and products and fit:
    from .feed_filters import product_passes_size

    for p in products:
      if not product_is_feed_eligible(p):
        continue
      if not product_passes_source_rules(p, rules_by_source):
        continue
      if not product_gender_compatible(p, fit.gender_target):
        continue
      if not product_passes_size(p, fit):
        continue
      filtered.append(p)
      if len(filtered) >= 500:
        break

  score_cap = max(30, min(1000, int(max_to_score)))
  score_pool = filtered[:score_cap]
  ctx = build_feed_context(db, user, product_ids=[p.id for p in score_pool])
  scored = [score_product(db, user, p, ctx) for p in score_pool]
  scored.sort(key=lambda x: x.final_score, reverse=True)
  lim = max(1, min(100, int(limit)))
  # Diversity Layer поверх отсортированного списка, затем срез
  top = _apply_diversity(scored[: lim * 3])[:lim]
  from .recommendation_cache_service import persist_recommendation_caches  # noqa: PLC0415

  persist_recommendation_caches(
    db,
    user_id=user.id,
    filtered_products=filtered,
    scored_top=top,
  )
  return top
