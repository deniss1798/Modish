"""Pure admission and ordering rules for completing one specific garment.

The returned products are options for missing outfit roles, never substitutes
for the anchor. Style preferences order valid options without inventing size,
stock or colour certainty.
"""
import re

from ..catalog_normalize import normalize_colors_value
from .catalog.product_dna import reliable_taste_features
from .garment_roles import product_role
from .outfit_quality import is_sports_item, pair_compatible, scenario_product_ok


COMPLEMENT_ROLES = {
    'top': ('bottom', 'shoes'),
    'bottom': ('top', 'shoes'),
    'shoes': ('top', 'bottom'),
    'one_piece': ('shoes',),
    'outerwear': ('top', 'bottom', 'shoes'),
}
_NEUTRALS = {'black', 'white', 'cream', 'grey', 'navy', 'brown'}


def complement_scenario(anchor) -> str:
    if is_sports_item(anchor):
        return 'gym'
    features = reliable_taste_features(anchor, dimensions=('occasion', 'formality'))
    occasions = features.get('occasion', set())
    # Multiple documented occasions are not evidence of a single dress code.
    if len(occasions) == 1:
        return {'work': 'office', 'evening': 'evening', 'wedding': 'wedding',
                'date': 'date', 'vacation': 'vacation'}.get(next(iter(occasions)), 'daily')
    if features.get('formality') == {'formal'}:
        return 'evening'
    # Russian inflected adjectives are common in titles but not all feed
    # taxonomies. Restrict this fallback to the title of the sold garment.
    title = str(anchor.title or '').lower().replace('ё', 'е')
    title = re.split(r'\s+(?:с|со|к|под|для|with|for)\s+', title, maxsplit=1)[0]
    if re.search(r'(?<!\w)(?:вечерн\w*|торжествен\w*)(?!\w)', title):
        return 'evening'
    if re.search(r'(?<!\w)(?:офисн\w*|делов\w*)(?!\w)', title):
        return 'office'
    return 'daily'


def complements_anchor(anchor, product, *, scenario: str | None = None) -> bool:
    """Semantic checks shared by ranking and current-evidence publication."""
    return (product_role(product) in COMPLEMENT_ROLES.get(product_role(anchor), ())
            and pair_compatible(anchor, product)
            and scenario_product_ok(product, scenario or complement_scenario(anchor)))


def complement_style_score(anchor, product) -> int:
    """Prefer an easy colour bridge over a similarly priced random item.

    These are ordering preferences, not claims that two arbitrary colours
    cannot be worn together. Missing colour/pattern evidence stays neutral.
    """
    anchor_colors = set(normalize_colors_value(anchor.colors or anchor.color_original))
    colors = set(normalize_colors_value(product.colors or product.color_original))
    score = 0
    if anchor_colors and colors:
        if anchor_colors & colors:
            score += 4
        if colors <= _NEUTRALS:
            score += 3
        elif anchor_colors <= _NEUTRALS:
            score += 1
    anchor_features = reliable_taste_features(anchor, dimensions=('pattern', 'style', 'season'))
    features = reliable_taste_features(product, dimensions=('pattern', 'style', 'season'))
    for dimension in ('style', 'season'):
        if anchor_features.get(dimension, set()) & features.get(dimension, set()):
            score += 1
    # A single printed statement item is easier to complete with a plain one.
    printed = anchor_features.get('pattern', set()) - {'solid'}
    if printed and features.get('pattern') == {'solid'}:
        score += 2
    return score
