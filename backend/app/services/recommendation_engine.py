"""MIE product recommendation orchestrator.

Keeps the legacy public facade while delegating candidate retrieval and
structured MIE scoring to dedicated services.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from ..catalog_normalize import canon_colors_from_text, normalize_category
from ..models import FitProfile, Product, RecommendationEventV2, StyleProfile, TasteProfile, User
from .catalog.rule_filters import load_rules_by_source_id, product_gender_compatible, product_passes_source_rules
from .catalog.catalog_quality import product_is_feed_eligible
from .candidate_retrieval_service import retrieve_candidates
from .feed_filters import product_passes_hard_filters
from .mie_scoring import TasteFeatureMap, compute_mie_score, load_user_taste_feature_map
from ..schemas.photo_analysis import extract_analysis_section


@dataclass(frozen=True)
class ScoredProduct:
  product: Product
  final_score: float
  breakdown: dict[str, float]
  reason: str
  reasons: list[str]
  candidate_sources: list[str] = field(default_factory=list)


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
  taste_features: TasteFeatureMap = field(default_factory=dict)
  candidate_sources: dict[str, set[str]] = field(default_factory=dict)
  scenario: str = "daily"


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


def build_feed_context(
  db: Session,
  user: User,
  *,
  product_ids: list[str] | None = None,
  scenario: str = "daily",
) -> FeedContext:
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
    taste_features=load_user_taste_feature_map(db, user_id=user.id),
    scenario=(scenario or "daily").strip().lower() or "daily",
  )


def score_product(db: Session, user: User, product: Product, ctx: FeedContext | None = None) -> ScoredProduct:
  """
  MIE v4 score facade:
  FitScore, TasteScore, ContextScore, QualityScore, ExplorationScore
  with legacy TasteProfile weights as a compatibility fallback.
  """
  if ctx is None:
    ctx = build_feed_context(db, user, product_ids=[product.id])

  mie = compute_mie_score(
    product,
    taste=ctx.taste,
    fit=ctx.fit,
    palette=ctx.palette,
    avoid_colors=ctx.avoid_colors,
    taste_features=ctx.taste_features,
    seen_count=int(ctx.seen_counts.get(product.id, 0)),
    popularity=float(ctx.popularity.get(product.id, 0.0)),
    scenario=ctx.scenario,
  )
  bd: dict[str, float] = {
    "fit_score": mie.fit_score,
    "taste_score": mie.taste_score,
    "context_score": mie.context_score,
    "quality_score": mie.quality_score,
    "exploration_score": mie.exploration_score,
    "final_score": mie.final_score,
    "hard_reject": 1.0 if mie.hard_reject else 0.0,
    "candidate_source_count": float(len(ctx.candidate_sources.get(product.id, set()))),
    **mie.debug,
  }
  reason = mie.reasons[0] if mie.reasons else "Подобрано под ваш профиль"
  return ScoredProduct(
    product=product,
    final_score=mie.final_score,
    breakdown=bd,
    reason=reason,
    reasons=mie.reasons[:8],
    candidate_sources=sorted(ctx.candidate_sources.get(product.id, set())),
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
  scenario: str = "daily",
  max_to_score: int = 700,
) -> list[ScoredProduct]:
  exclude_product_ids = expand_product_exclusions(db, exclude_product_ids)
  base_ctx = build_feed_context(db, user, scenario=scenario)
  retrieval = retrieve_candidates(
    db,
    user=user,
    fit=base_ctx.fit,
    taste=base_ctx.taste,
    taste_features=base_ctx.taste_features,
    exclude_product_ids=exclude_product_ids,
    source=source,
    scenario=scenario,
    max_candidates=max_to_score,
  )
  products = retrieval.products
  rules_by_source = load_rules_by_source_id(db)
  fit = base_ctx.fit
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

  score_cap = max(30, min(1500, int(max_to_score)))
  score_pool = filtered[:score_cap]
  ctx = build_feed_context(db, user, product_ids=[p.id for p in score_pool], scenario=scenario)
  ctx.candidate_sources = retrieval.sources_by_product_id
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
