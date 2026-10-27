"""Literal catalogue search across product fields, before pagination."""
import re
from sqlalchemy import String, cast, func, or_
from ..models import Product
from .catalog_filters import CATEGORY_GROUPS, CATEGORY_TERMS
from .catalog.fashion_taxonomy import PATTERNS
from .shopping_footwear import FOOTWEAR_RE, FOOTWEAR_SEARCH_TERMS, footwear_types, product_footwear_types
from .garment_roles import product_role

# Small explicit vocabulary, not a stemmer applied to arbitrary brand names.
_ROOTS = {
    'куртка': 'куртк', 'куртки': 'куртк', 'джинсы': 'джинсы',
    'брюки': 'брюк', 'рубашка': 'рубаш', 'рубашки': 'рубаш',
    'платье': 'плать', 'платья': 'плать', 'футболка': 'футболк',
    'футболки': 'футболк', 'юбка': 'юбк', 'юбки': 'юбк',
    'кроссовки': 'кроссов', 'ботинки': 'ботин', 'сапоги': 'сапог',
    'сандалии': 'сандал', 'туфли': 'туфл', 'босоножки': 'босонож',
}
_GROUPS = {'верхняя одежда': 'outerwear', 'обувь': 'shoes'}
_COLORS = {
    'черн': ('black',), 'бел': ('white',),
    'син': ('blue', 'navy'), 'голуб': ('blue',),
    'сер': ('grey',), 'бежев': ('cream',),
    'красн': ('red',), 'зелен': ('green',),
    'коричнев': ('brown',), 'розов': ('pink',),
}


def matches_search_product(product, query: str | None) -> bool:
    """Confirm concrete shoe nouns after broad SQL retrieval, before paging.

    Merchant navigation can mention both pumps and moccasins. It retrieves
    candidates but cannot prove that the specific offer is the requested type.
    """
    text = ' '.join(str(query or '').lower().replace('ё', 'е').split()[:12])
    required = footwear_types(text)
    return (not required or (product_role(product) == 'shoes'
            and required <= product_footwear_types(product)))


def search_conditions(query: str):
    text = ' '.join(query.lower().replace('ё', 'е').split())
    # Deliberately exclude description: merchant cross-selling is not a match.
    fields = [func.replace(func.lower(func.coalesce(column, '')), 'ё', 'е') for column in (
        Product.title, Product.brand, Product.category, Product.category_name,
        Product.subcategory, Product.color_original, Product.color_family,
        cast(Product.colors, String),
    )]

    def contains_any(terms):
        return or_(*(field.contains(term, autoescape=True) for term in terms for field in fields))

    if text in _GROUPS:
        group = _GROUPS[text]
        return [contains_any((*CATEGORY_GROUPS[group], *CATEGORY_TERMS[group]))]
    if text == 'трикотаж':
        return [contains_any(('трикотаж', 'джемпер', 'свитер', 'кардиган', 'водолаз'))]
    conditions = []
    for token in text.split()[:12]:
        terms = (_ROOTS.get(token, token),)
        for kind, pattern in FOOTWEAR_RE.items():
            if pattern.fullmatch(token):
                terms = FOOTWEAR_SEARCH_TERMS[kind]
                break
        color_values = next((values for root, values in _COLORS.items() if re.fullmatch(
            root + r'(?:ый|ая|ое|ые|ого|ой|ых|ую|ий|яя|ее|ие|его|ей|их|юю|ем|ыми|ими)', token)), None)
        if color_values:
            # A short stem such as «син» also occurs in «мокасины»; brands
            # (Serginnetti for grey) and category names are not colour evidence.
            pattern = r'(?<!\w)(?:' + '|'.join(PATTERNS['color'][value] for value in color_values) + r')(?!\w)'
            color_fields = (fields[0], *fields[5:])
            conditions.append(or_(*(field.regexp_match(pattern) for field in color_fields)))
        else:
            conditions.append(contains_any(terms))
    return conditions


def personalized_search(db, user, activation, query, *, limit=50, offset=0, q=None):
    """Rank only literal-query matches; do not substitute a recommendation feed."""
    import heapq
    from .activation_policy import product_ok
    from .personalization_scoring import AffinityScorer, load_ranking_seen_counts
    from .profile_repository import load_preferences
    from .catalog.rule_filters import load_rules_by_source_id
    from .mie_scoring import load_user_taste_feature_map
    from .product_identity import product_identity_keys
    from .recommendation_budget import check_ranking_budget
    profile, fit, taste = load_preferences(db, user)
    taste_features = load_user_taste_feature_map(db, user_id=user.id)
    scorer = AffinityScorer(profile, taste, taste_features=taste_features, fit=fit,
        seen_counts=load_ranking_seen_counts(db, user_id=user.id))
    rules = load_rules_by_source_id(db)
    start, page_size = max(0, offset), min(200, max(1, limit))
    count = start + page_size
    best, seen = [], set()
    with db.scalars(query.execution_options(yield_per=200)) as products:
        for product in products:
            check_ranking_budget(db)
            if not matches_search_product(product, q):
                continue
            if not product_ok(product, fit, activation, rules=rules):
                continue
            keys = product_identity_keys(product)
            if keys & seen:
                continue
            seen.update(keys)
            score = scorer.score(product)
            entry = (score, product.id, product)
            if len(best) < count:
                heapq.heappush(best, entry)
            elif entry[:2] > best[0][:2]:
                heapq.heapreplace(best, entry)
    check_ranking_budget(db)
    return [item[2] for item in sorted(best, key=lambda x: (x[0], x[1]), reverse=True)[start:]]
