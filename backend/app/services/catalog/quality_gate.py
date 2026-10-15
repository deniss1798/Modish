"""Conservative admission of commerce facts; no network or model inference."""
from dataclasses import dataclass
import re
from ...money import as_rubles

from ...catalog_normalize import normalize_category
from ..catalog_audience import product_is_adult
from ..catalog_scope import product_is_fashion
from .affiliate_link_service import _public_web_url, resolve_outbound_url
from .stock_freshness import MAX_STOCK_AGE, stock_observation_problem
from .size_resolver import resolve_size

SUPPORTED_CATEGORIES = frozenset({
    "футболки", "рубашки", "джемперы", "худи", "джинсы", "брюки", "шорты",
    "юбки", "платья", "верхний_слой", "обувь", "костюмы", "комбинезоны",
})


@dataclass(frozen=True)
class QualityDecision:
    status: str
    reasons: tuple[str, ...]


def recognized_size(value, *, footwear=False, size_system=None):
    resolved = resolve_size(value, size_system=size_system, category='обувь' if footwear else '')
    token = resolved.normalized_size or ''
    if not footwear and re.fullmatch(r"XXXS|XXS|XS|S|M|L|XL|XXL|XXXL|[2-6]XL|W\d{2}(?:L\d{2})?", token):
        return True
    if re.fullmatch(r"\d{1,2}(?:\.5)?", token):
        amount = float(token)
        system = resolved.size_system
        bounds = ({'EU': (34, 50), 'RU': (34, 50), 'US': (1, 18), 'UK': (1, 16)} if footwear else
            {'RU': (24, 80), 'EU': (28, 66), 'US': (0, 32), 'UK': (2, 36)})
        low, high = bounds.get(system, (34, 50) if footwear else (24, 74))
        return low <= amount <= high
    return False


def evaluate_product(product, *, checked_at=None):
    reasons = []
    if not str(product.source or "").strip():
        reasons.append("missing_merchant")
    if not str(product.title or "").strip() or product.title == "Товар":
        reasons.append("missing_title")
    if not product_is_adult(product):
        reasons.append("childrenswear")
    category = normalize_category(product.category)
    if not product_is_fashion(product) or category not in SUPPORTED_CATEGORIES:
        reasons.append("unsupported_category")
    if product.gender_target not in {"womenswear", "menswear", "unisex"}:
        reasons.append("unknown_gender")
    if not product.available_sizes:
        reasons.append("missing_size")
    elif not all(recognized_size(s, footwear=category == "обувь", size_system=_size_system(product)) for s in product.available_sizes):
        reasons.append("unrecognized_size")
    if not 0 < as_rubles(product.price) <= 10_000_000:
        reasons.append("invalid_price")
    if product.currency not in {"RUB", "RUR"}:
        reasons.append("unsupported_currency")
    if not _public_web_url(product.image_url):
        reasons.append("invalid_image_url")
    if not resolve_outbound_url(product):
        reasons.append("invalid_product_url")
    if product.availability_status not in {"in_stock", "out_of_stock"}:
        reasons.append("unknown_availability")
    if product.availability_status == "in_stock":
        problem = stock_observation_problem(product, checked_at=checked_at)
        if problem:
            reasons.append(problem)
    if reasons:
        return QualityDecision("quarantined", tuple(reasons))
    if product.availability_status == "out_of_stock":
        return QualityDecision("unavailable", ("out_of_stock",))
    return QualityDecision("passed", ())


def _size_system(product):
    from .size_resolver import explicit_size_system
    return explicit_size_system(product.feed_raw_json, product.raw_params_json)
