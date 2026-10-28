"""Classify the sold garment; cross-selling words and photographs are not roles."""
import re
from functools import lru_cache

from ..catalog_normalize import normalize_category


def _noun(pattern: str):
    return re.compile(r'(?<!\w)(?:' + pattern + r')(?!\w)')


# Earliest specific noun wins. Testing for *any* skirt/coat before tops turns
# "Блузка к юбке" into a bottom and "Юбка с кардиганом" into outerwear.
_ROLE_NOUNS = (
    ('one_piece', _noun(r'плать\w*|сарафан\w*|dress(?:es)?')),
    ('outerwear', _noun(r'куртк\w*|пальто|пуховик\w*|ветров\w*|парк[аиуой]+|плащ(?:и|а|у|ом|е|ей|ами|ах)?|жилет(?:ы|а|у|ом|е|ов|ами|ах)?|кардиган(?:ы|а|у|ом|е|ов|ами|ах)?|жакет(?:ы|а|у|ом|е|ов|ами|ах)?|пиджак\w*|блейзер\w*|шуб\w*|дубленк\w*|coat(?:s)?|jacket(?:s)?|cardigan(?:s)?|parka(?:s)?|blazer(?:s)?|vest(?:s)?')),
    ('bottom', _noun(r'брюк\w*|штан\w*|джинс(?:ы|ов|ам|ами|ах)|джоггер\w*|шорт\w*|юбк\w*|леггин\w*|легин\w*|лосин\w*|pants|trousers|jeans|joggers|shorts|skirts?|leggings')),
    ('top', _noun(r'рубашк\w*|сорочк\w*|поло|футболк\w*|майк\w*|блуз(?:а|ы|у|ой|е|ами|ах|к\w*)|джемпер(?:ы|а|у|ом|е|ов|ами|ах)?|свитер(?:ы|а|у|ом|е|ов|ами|ах)?|свитшот(?:ы|а|у|ом|е|ов|ами|ах)?|толстовк\w*|худи|водолазк\w*|лонгслив\w*|пуловер(?:ы|а|у|ом|е|ов|ами|ах)?|топ(?:ы|а|у|ом|е|ов|ами|ах)?|shirts?|polo|t[ -]shirts?|tops?|sweaters?|hoodies?|sweatshirts?|tanks?|blouses?|pullovers?')),
    ('shoes', _noun(r'туфл\w*|лофер\w*|мокасин\w*|кроссов\w*|кед[ыа]?|ботин\w*|ботильон\w*|сапог\w*|сандал\w*|босонож\w*|балетк\w*|слипон\w*|эспадрил\w*|брог\w*|оксфорд\w*|дерби|мюли|сабо|sneakers?|loafers?|boots?|pumps?|flats?|sandals?|mules?|oxfords?|brogues?')),
)
_UNMODELED = _noun(r'комбинез\w*|полукомбинез\w*|комплект(?:ы|а|у|ом|е|ов|ами|ах)?|костюм(?:ы|а|у|ом|е|ов|ами|ах)?|ремень|ремни|сумк\w*|рюкзак\w*|чехол\w*|бюстгальтер\w*|боди|купаль\w*|пижам\w*|носк\w*|колгот\w*|suits?|jumpsuits?|overalls|dungarees|sets?|belts?|bags?|bras?|bodysuits?|swimsuits?')
_DRESS_SHOES = _noun(r'dress[ -]shoes')
_COMPOUND_DRESS = _noun(r'(?:shirt|sweater|sweatshirt|tank|t[ -]shirt|blazer)[ -]dress(?:es)?')
_DRESS_SEPARATE = _noun(r'dress[ -](?P<item>shirts?|pants|trousers|skirts?)')
_GENERIC_SHOES = _noun(r'shoes')
_CATEGORY_ROLES = (
    ('one_piece', {'платья'}),
    ('shoes', {'обувь'}),
    ('bottom', {'брюки', 'джинсы', 'шорты', 'юбки'}),
    ('top', {'футболки', 'рубашки', 'джемперы', 'худи', 'блузки', 'топы'}),
)


def product_role(product) -> str | None:
    # Cache only immutable source fields, never an ORM object. A live title or
    # category edit therefore gets its own classification immediately.
    fields = tuple(str(getattr(product, name, '') or '') for name in
                   ('title', 'subcategory', 'category_name', 'category'))
    return _role_from_fields(fields)


@lru_cache(maxsize=4096)
def _role_from_fields(fields: tuple[str, ...]) -> str | None:
    title = fields[0].lower().replace('ё', 'е')
    matches = [(match.start(), role) for role, pattern in _ROLE_NOUNS
               if (match := pattern.search(title)) is not None]
    # "Dress shoes" is footwear, while "DC Shoes Tank" still sells a tank.
    if (match := _DRESS_SHOES.search(title)) is not None:
        matches = [(pos, role) for pos, role in matches if pos != match.start()]
        matches.append((match.start(), 'shoes'))
    if (match := _COMPOUND_DRESS.search(title)) is not None:
        matches = [(pos, role) for pos, role in matches if pos != match.start()]
        matches.append((match.start(), 'one_piece'))
    if (match := _DRESS_SEPARATE.search(title)) is not None:
        matches = [(pos, role) for pos, role in matches if pos != match.start()]
        matches.append((match.start(), 'top' if match.group('item').startswith('shirt') else 'bottom'))
    if matches:
        position, role = min(matches)
        if (unmodeled := _UNMODELED.search(title)) is not None and unmodeled.start() < position:
            return None
        return role
    if _UNMODELED.search(title):
        return None
    if _GENERIC_SHOES.search(title):
        return 'shoes'
    # When a title is only a merchant/model name, prefer the most specific
    # category leaf to a stale broad parent. Explicit sold nouns above always
    # win (a "Майка DC Shoes" cannot become footwear from its subcategory).
    for raw_category in fields[1:]:
        category = normalize_category(raw_category)
        if category in {'костюмы', 'комбинезоны'}:
            return None
        for role, values in _CATEGORY_ROLES:
            if category in values:
                return role
    return None
