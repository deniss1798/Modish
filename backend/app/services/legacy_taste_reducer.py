"""Pure legacy list/weight changes, shared by persistence and ranking preview."""
from copy import deepcopy
from dataclasses import dataclass

from .recommendation_config import (NEGATIVE_LIST_EVENTS, POSITIVE_LIST_EVENTS,
    TASTE_UPDATE_EVENTS, event_weight, feature_targets_for_event,
    legacy_feature_signal_multiplier)

LIST_FIELDS = tuple(f'{prefix}_{kind}' for prefix in ('liked', 'disliked')
    for kind in ('categories', 'brands', 'colors', 'styles'))
WEIGHT_FIELDS = tuple(f'{kind}_weights' for kind in ('category', 'brand', 'color', 'style'))


@dataclass(frozen=True)
class LegacyProductFacts:
    categories: tuple[str, ...]
    category: str
    brand: str
    colors: tuple[str, ...]
    styles: tuple[str, ...]


def legacy_product_facts(product):
    from ..catalog_normalize import normalize_colors_value
    from .catalog.style_tagger import infer_style_tags
    from .recommendation_engine import _product_category_norms
    categories = _product_category_norms(product)
    return LegacyProductFacts(tuple(categories), next(iter(categories), '') or
        (product.category or '').strip().lower(), (product.brand or '').strip().lower(),
        tuple(normalize_colors_value(product.colors or product.color_original)),
        tuple(str(tag).strip().lower() for tag in (product.style_tags or
            infer_style_tags(title=product.title, category=product.category)) if str(tag).strip()))


def reduce_legacy_taste(taste, facts, *, event_type, meta, weight=None):
    """Return fresh values; never mutate either the profile or product facts."""
    result = {name: deepcopy(list(getattr(taste, name) or [])) for name in LIST_FIELDS}
    result.update({name: deepcopy(dict(getattr(taste, name) or {})) for name in WEIGHT_FIELDS})

    def add_unique(values, value):
        if value and value not in values:
            values.append(value)

    def bump(values, key, delta):
        if not key:
            return
        try:
            current = float(values.get(key, 0))
        except Exception:
            current = 0.0
        updated = current + float(delta)
        if updated > 50:
            updated = 50.0
        if updated < -50:
            updated = -50.0
        values[key] = updated

    def prune(values):
        if len(values) <= 200:
            return
        items = []
        for key, value in values.items():
            try:
                items.append((str(key), float(value)))
            except Exception:
                continue
        items.sort(key=lambda item: abs(item[1]), reverse=True)
        keep = {key for key, _ in items[:200]}
        for key in list(values):
            if str(key) not in keep:
                values.pop(key, None)

    prefix = ('liked' if event_type in POSITIVE_LIST_EVENTS or
        (meta.get('surface') == 'style_swipe' and event_type == 'like') else
        'disliked' if event_type in NEGATIVE_LIST_EVENTS else None)
    if prefix:
        for kind, values in (('categories', (facts.category,)), ('brands', (facts.brand,)),
                ('colors', facts.colors[:3]), ('styles', facts.styles[:3])):
            for value in values:
                add_unique(result[f'{prefix}_{kind}'], value)
    if event_type in TASTE_UPDATE_EVENTS:
        delta = (event_weight(event_type) if weight is None else weight) * legacy_feature_signal_multiplier(event_type, meta)
        targets = feature_targets_for_event(event_type, meta)
        for kind, values in (('category', facts.categories or (facts.category,)), ('brand', (facts.brand,)),
                ('color', facts.colors[:3]), ('style', facts.styles[:3])):
            if kind in targets:
                for value in values:
                    bump(result[f'{kind}_weights'], value, delta)
        for name in WEIGHT_FIELDS:
            prune(result[name])
    return result
