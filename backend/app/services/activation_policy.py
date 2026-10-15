"""Pure eligibility rules and shared features for the initial fashion segment."""
import re
from ..money import as_rubles
from ..catalog_normalize import normalize_category, normalize_colors_value
from .catalog.catalog_quality import product_is_feed_eligible
from .catalog.affiliate_link_service import _public_web_url
from .catalog.rule_filters import product_passes_source_rules
from .catalog.style_tagger import infer_style_tags
from .garment_roles import product_role
from .personal_size_gate import evaluate_personal_size
from .product_eligibility import evaluate_eligibility

STYLES = {'casual', 'smart_casual', 'minimalism', 'romantic', 'elegant', 'streetwear'}
STYLE_LABELS = {'casual': 'Повседневный стиль', 'smart_casual': 'Smart casual', 'minimalism': 'Минимализм', 'romantic': 'Романтичный стиль', 'elegant': 'Элегантный стиль', 'streetwear': 'Свободный городской стиль'}
CATEGORIES = ('футболки', 'рубашки', 'джемперы', 'джинсы', 'брюки', 'юбки', 'платья', 'обувь', 'верхний_слой')
SEGMENT_BUDGETS = {'economy': 4000, 'mass': 10000, 'mid': 25000}
EXCLUDED_SOURCES = {'vipavenue', 'vip_avenue', 'vip avenue', 'tsumoutlet', 'demix', 'sportmaster', 'demo'}


def features(product):
    styles = set(product.style_tags or infer_style_tags(title=product.title, category=product.category))
    return {'style': styles & STYLES, 'category': {normalize_category(product.category or product.category_name or '')},
            'color': set(normalize_colors_value(product.colors or product.color_original)), 'brand': {str(product.brand or '').lower()}}


def confirmed_size(product, fit, session):
    return evaluate_personal_size(product, fit).eligible


def product_ok(product, fit, session, *, rules=None):
    from .outfit_quality import is_sports_item
    if not fit or not session or fit.gender_target != 'womenswear' or product.gender_target != fit.gender_target:
        return False
    if str(product.source or '').lower() in EXCLUDED_SOURCES:
        return False
    text = str(product.title or '').lower()
    if re.search(r'спортив|трениров|фитнес|для бега|термобель|хоккей|баскетбол', text) or is_sports_item(product):
        return False
    if product_role(product) not in {'top', 'bottom', 'one_piece', 'shoes', 'outerwear'}:
        return False
    if str(product.currency or '').upper() not in {'RUB', 'RUR'}:
        return False
    if not 300 <= as_rubles(product.price) <= min(25000, as_rubles(fit.budget_max)):
        return False
    return (evaluate_eligibility(product, fit).eligible and bool(_public_web_url(product.image_url))
            and (rules is None or product_passes_source_rules(product, rules)))
