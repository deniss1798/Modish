"""Request-local scoring shared by feed, search and outfits.

Create one scorer per request after loading preferences. Products/preferences
must not be mutated during that request. The bounded cache never crosses users
or requests, and holds no database session. Eligibility is checked separately.
The five factors are bounded rule scores, not measured probabilities. Soft Fit
compares garment evidence with profile recommendations; Quality measures card
completeness. Neither predicts physical fit or visual quality from missing data.
"""
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from ..models import Product, RecommendationEventV2, StyleProfile, TasteProfile
from .activation_policy import features
from .catalog.fashion_taxonomy import TAXONOMY
from .catalog.product_dna import reliable_taste_features
from .mie_scoring import TasteFeatureMap, compute_taste_score, profile_confidence
from .recommendation_context import ShoppingContext
from .recommendation_config import FINAL_SCORE_WEIGHTS
from .user_twin_service import DNA_TASTE_DIMENSIONS, product_taste_features


@dataclass(frozen=True)
class AffinityEvaluation:
    affinity: float
    taste_score: float
    taste_reasons: tuple[str, ...]
    context_score: float
    fit_score: float
    quality_score: float
    exploration_score: float
    fit_reasons: tuple[str, ...] = ()
    context_reasons: tuple[str, ...] = ()
    quality_reasons: tuple[str, ...] = ()
    exploration_reasons: tuple[str, ...] = ()

    @property
    def breakdown(self):
        return {name: getattr(self, name) for name in FINAL_SCORE_WEIGHTS}


def load_ranking_seen_counts(db, *, user_id, as_of=None):
    """Real visible actions, deduplicated by product/day; delivery is not sight."""
    from .behavior_profile import EXPOSURE_EVENTS
    now = as_of or datetime.now(timezone.utc)
    rows = db.execute(select(RecommendationEventV2.product_id,
        func.count(func.distinct(func.date(RecommendationEventV2.created_at))))
        .where(RecommendationEventV2.user_id == user_id,
            RecommendationEventV2.product_id.is_not(None),
            RecommendationEventV2.event_type.in_(EXPOSURE_EVENTS),
            RecommendationEventV2.created_at >= now - timedelta(days=90),
            RecommendationEventV2.created_at <= now)
        .group_by(RecommendationEventV2.product_id))
    return {pid: count for pid, count in rows}


def _clamp(value):
    return max(0.0, min(1.0, value))


def _fit_values(fit, field, dimension):
    return frozenset(str(value).strip().lower() for value in (getattr(fit, field, None) or [])
        if str(value).strip().lower() in TAXONOMY[dimension])


def _quality(product):
    """Observable card completeness, never an inferred visual-quality metric."""
    def present(field):
        return bool(str(getattr(product, field, None) or '').strip())
    score = sum(weight for has_value, weight in (
        (present('title'), .15), (present('category') or present('category_name'), .15),
        (present('image_url'), .20),
        (present('affiliate_url') or present('product_url') or present('original_url'), .20),
        (present('brand'), .15), (present('description'), .15)) if has_value)
    reasons = ('В карточке указаны бренд и описание',) if present('brand') and present('description') else ()
    return _clamp(score), reasons


def _base_affinity(product, profile, taste):
    values = features(product)
    score = 0.25 + 0.35 * bool(values['style'] & set(profile.style_preferences or []))
    for kind, scale in [('style', .22), ('category', .10), ('color', .08), ('brand', .05)]:
        known_brands = getattr(taste, 'behavior_snapshot', {}).get('brands', {}) if kind == 'brand' else {}
        weights = getattr(taste, f'{kind}_weights') or {}
        signals = [max(-1.0, min(1.0, float(weights.get(value, 0)) / 6)) for value in values[kind] if value not in known_brands]
        score += scale * (sum(signals) / len(signals) if signals else 0)
    from .behavior_profile import behavior_adjustment
    score += behavior_adjustment(product, getattr(taste, 'behavior_snapshot', {}))
    return score


class AffinityScorer:
    def __init__(self, profile: StyleProfile, taste: TasteProfile, *,
                 taste_features: TasteFeatureMap | None = None,
                 context: ShoppingContext | None = None, fit=None, seen_counts=None,
                 preferred_colors=(), preferred_styles=(), requested_categories=(), cache_size: int = 512):
        if cache_size < 1:
            raise ValueError('cache_size must be positive')
        self.profile = profile
        self.taste = taste
        self.taste_features = dict(taste_features or {})
        self.context = context
        self.confidence = profile_confidence(self.taste_features)
        self.preferred_colors = frozenset(preferred_colors)
        self.preferred_styles = frozenset(preferred_styles)
        self.requested_categories = frozenset(requested_categories)
        self.cache_size = cache_size
        self._cache: OrderedDict[Product, AffinityEvaluation] = OrderedDict()
        self.fit = None
        self.seen_counts = {}
        self.seen_counts_loaded = False
        self.bind_admission_context(fit=fit, seen_counts=seen_counts)

    def bind_admission_context(self, *, fit, seen_counts=None):
        """Bind shared request inputs before ranking, including outfit callers.

        Snapshot only explicit supported recommendations. Size, default regular
        fit and body measurements do not establish construction preference or
        prove physical garment fit. Changing inputs invalidates local scores.
        """
        recommended = _fit_values(fit, 'recommended_silhouettes', 'silhouette')
        avoid = _fit_values(fit, 'avoid_silhouettes', 'silhouette')
        avoid_fit = _fit_values(fit, 'avoid_fit', 'fit')
        self.fit = fit
        self._recommended_silhouettes = recommended - avoid
        self._avoid_silhouettes = avoid
        self._avoid_fit = avoid_fit
        if seen_counts is not None:
            self.seen_counts = dict(seen_counts)
            self.seen_counts_loaded = True
        # Unlearned construction dimensions cannot affect score or reasons.
        # Context has its own evidence needs, independent of persistent taste.
        dimensions = {kind for kind, _ in self.taste_features if kind in DNA_TASTE_DIMENSIONS}
        if self.context is not None:
            dimensions.update(('style', 'occasion', 'season', 'formality'))
        if recommended or avoid:
            dimensions.add('silhouette')
        if avoid_fit:
            dimensions.add('fit')
        if self.preferred_colors:
            dimensions.add('color')
        if self.preferred_styles:
            dimensions.add('style')
        self._dna_dimensions = tuple(sorted(dimensions))
        self._cache.clear()

    @property
    def cached_products(self) -> int:
        return len(self._cache)

    def evaluate(self, product: Product) -> AffinityEvaluation:
        cached = self._cache.get(product)
        if cached is not None:
            self._cache.move_to_end(product)
            return cached
        dna = reliable_taste_features(product, dimensions=self._dna_dimensions) if self._dna_dimensions else {}
        product_features = product_taste_features(product, dna_features=dna)
        twin, reasons, _ = compute_taste_score(product, taste=self.taste,
            taste_features=self.taste_features, product_features=product_features,
            confidence=self.confidence)
        taste_score = _base_affinity(product, self.profile, self.taste)
        if self.taste_features:
            taste_score += .3 * (twin - .5)
        taste_score = _clamp(taste_score)
        contextual, context_reasons = self.context.score(product, semantics=dna) if self.context else (.5, [])
        context_parts = [(contextual, 1.0)]
        for wanted, known, weight, reason in (
            (self.preferred_colors, dna.get('color', set()), .35, 'Цвет совпадает с пожеланием текущего запроса'),
            (self.preferred_styles, dna.get('style', set()), .35, 'Стиль совпадает с пожеланием текущего запроса'),
            (self.requested_categories, product_features.get('category', set()), .20, 'Категория соответствует текущему запросу')):
            if wanted:
                matched = bool(wanted & known)
                context_parts.append((float(matched), weight))
                if matched:
                    context_reasons.append(reason)
        contextual = sum(value * weight for value, weight in context_parts) / sum(weight for _, weight in context_parts)
        fit_score, fit_reasons = .5, []
        silhouettes = dna.get('silhouette', set())
        if silhouettes & self._recommended_silhouettes:
            fit_score += .20
            fit_reasons.append('Силуэт соответствует рекомендациям вашего профиля')
        if silhouettes & self._avoid_silhouettes:
            fit_score -= .25
            fit_reasons.append('Этот силуэт отмечен в профиле как нежелательный')
        if dna.get('fit', set()) & self._avoid_fit:
            fit_score -= .20
            fit_reasons.append('Эта посадка отмечена в профиле как нежелательная')
        fit_score = _clamp(fit_score)
        quality_score, quality_reasons = _quality(product)
        # Exploration is a small deterministic novelty prior, never random
        # reordering or a way to admit an unavailable/forbidden garment.
        known_positive = any(pref > 0 and confidence > .4 for kind, values in product_features.items()
            for value in values for pref, confidence in [self.taste_features.get((kind, str(value).lower()), (0, 0))])
        seen = self.seen_counts.get(product.id, 0)
        if ('item', str(product.id).lower()) in self.taste_features:
            seen = max(1, seen)
        exploration = _clamp(.65 * (1 - self.confidence) + .25 - min(.30, seen * .08) - .10 * known_positive)
        exploration_reasons = ('Немного расширяет подборку',) if seen == 0 and exploration >= .55 else ()
        parts = {'taste_score': taste_score, 'fit_score': fit_score, 'context_score': contextual,
            'quality_score': quality_score, 'exploration_score': exploration}
        score = _clamp(sum(FINAL_SCORE_WEIGHTS[name] * value for name, value in parts.items()))
        result = AffinityEvaluation(score, taste_score, tuple(reasons), contextual,
            fit_score, quality_score, exploration, tuple(fit_reasons), tuple(context_reasons),
            quality_reasons, exploration_reasons)
        self._cache[product] = result
        if len(self._cache) > self.cache_size:
            self._cache.popitem(last=False)
        return result

    def score(self, product: Product) -> float:
        return self.evaluate(product).affinity


def affinity_score(product, profile, taste, *, taste_features=None, context=None, fit=None, seen_counts=None):
    """Compatibility entry point; collection callers should reuse a scorer."""
    return AffinityScorer(profile, taste, taste_features=taste_features, context=context,
        fit=fit, seen_counts=seen_counts).score(product)
