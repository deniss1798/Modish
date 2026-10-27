"""Bounded Russian grammar. Unparsed requirements block results, never vanish.

No LLM, network, profile writes or guessed merchant facts. The API exposes the
parsed hard constraints so a user can correct them before following a link.
"""
import re
from decimal import Decimal
from ..money import as_rubles
from ..schemas.shopping_intent import IntentSpec, PARSER_VERSION
from .catalog.fashion_taxonomy import PATTERNS
from .shopping_footwear import FOOTWEAR_PATTERNS

CATEGORIES = {
    'брюки': r'брюк\w*|штан\w*', 'джинсы': r'джинс\w*',
    'платья': r'плать\w*', 'юбки': r'юбк\w*',
    'рубашки': r'рубаш\w*|блуз\w*', 'футболки': r'футбол\w*|топ(?:ы|а)?',
    'джемперы': r'свитер\w*|джемпер\w*|кардиган\w*|трикотаж\w*|водолаз\w*',
    'верхний_слой': r'пальто|куртк\w*|пуховик\w*|верхн\w*\s+одежд\w*',
    'обувь': r'обув\w*', 'костюмы': r'костюм\w*', 'шорты': r'шорт\w*',
}
OCCASIONS = {
    'office': r'офис\w*|работ\w*', 'wedding': r'свадьб\w*',
    'evening': r'вечер\w*|выход|корпоратив\w*', 'daily': r'каждый\s+день|повседневн\w*',
    'date': r'свидани\w*', 'vacation': r'отпуск\w*', 'walk': r'прогул\w*',
}
FEATURES = {('fit', 'oversized'): r'оверсайз\w*|oversized?',
    ('silhouette', 'wide'): r'широк\w*', ('silhouette', 'straight'): r'прям\w*',
    ('heel', 'heels'): r'каблук\w*|каблуков',
    **{('material', key): pattern for key, pattern in PATTERNS['material'].items()},
    **{('length', key): pattern for key, pattern in PATTERNS['length'].items()}}
FILLER = set('мне нужен нужны нужна нужно хочу ищу ищем найди найти покажи подбери собери собрать '
    'что надеть надену для в на до от и или но а без не только кроме желательно лучше '
    'предпочтительно пожалуйста нормальные нормальный нормальное нормальную цвет цвета '
    'рубль рублей рубля руб р одежда вещь вещи вариант варианты это этого этой такой такую '
    'такое такие более менее исключи исключить'.split())


def _matches(pattern, text):
    return re.finditer(r'(?<!\w)(?:' + pattern + r')(?!\w)', text)


def _modifier(text, start):
    clause = re.split(r'[,;.!?]|\b(?:но|а)\b', text[:start])[-1]
    if re.search(r'\b(?:не|без|кроме|исключи|исключить)\b', clause):
        return 'exclude'
    if re.search(r'\b(?:желательно|предпочтительно|лучше)\b', clause):
        return 'prefer'
    return 'require'


def parse_intent(text: str, *, previous: IntentSpec | None = None,
                 reference_price: Decimal | int | float | None = None, reference_colors=(), default_budget=None) -> IntentSpec:
    text = ' '.join(text.lower().replace('ё', 'е').split())
    result = previous.model_copy(deep=True) if previous else IntentSpec()
    result.parser_version = PARSER_VERSION
    result.unresolved = []
    consumed = [False] * len(text)

    def consume(match):
        consumed[match.start():match.end()] = [True] * (match.end() - match.start())

    def append(field, value):
        values = getattr(result, field)
        if value not in values:
            values.append(value)

    for match in _matches(r'образ\w*|комплект\w*|что\s+надеть', text):
        consume(match)
        result.intent_type = 'outfit'
    for match in _matches(r'(?:до|от)\s*\d+(?:[ \u00a0]\d{3})*(?:[.,]\d+)?\s*(?:тыс(?:яч\w*)?\.?|[кk])?', text):
        consume(match)
        raw = match.group()
        number = re.search(r'\d+(?:[ \u00a0]\d{3})*(?:[.,]\d+)?', raw).group()
        amount = float(number.replace(' ', '').replace('\u00a0', '').replace(',', '.'))
        amount *= 1000 if re.search(r'тыс|[кk]', raw) else 1
        if not amount.is_integer() or not 0 < amount <= 1_000_000:
            result.unresolved.append('Укажите бюджет от 1 до 1 000 000 ₽')
        else:
            setattr(result, 'budget_max' if raw.startswith('до') else 'budget_min', int(amount))
    requested = []
    # Keep the concrete shoe noun as a hard condition. Mapping every noun only
    # to the broad category would silently replace pumps with any footwear.
    for kind, pattern in FOOTWEAR_PATTERNS.items():
        for match in _matches(pattern, text):
            if all(consumed[match.start():match.end()]):
                continue
            consume(match)
            modifier = _modifier(text, match.start())
            target = result.excluded_features if modifier == 'exclude' else result.required_features
            target.setdefault('footwear_type', [])
            if kind not in target['footwear_type']:
                target['footwear_type'].append(kind)
            if modifier != 'exclude':
                requested.append('обувь')
    for category, pattern in CATEGORIES.items():
        for match in _matches(pattern, text):
            consume(match)
            if _modifier(text, match.start()) == 'exclude':
                append('excluded_categories', category)
            else:
                requested.append(category)
    if requested:
        result.requested_categories = sorted(set(requested))
        result.excluded_categories = [c for c in result.excluded_categories if c not in requested]
    for occasion, pattern in OCCASIONS.items():
        for match in _matches(pattern, text):
            consume(match)
            if _modifier(text, match.start()) == 'exclude':
                result.unresolved.append('Укажите нужный повод вместо исключённого')
            else:
                result.occasion = occasion
    for color, pattern in PATTERNS['color'].items():
        for match in _matches(pattern, text):
            consume(match)
            modifier = _modifier(text, match.start())
            append({'exclude': 'excluded_colors', 'prefer': 'preferred_colors'}.get(modifier, 'colors'), color)
    for (dimension, value), pattern in FEATURES.items():
        for match in _matches(pattern, text):
            consume(match)
            modifier = _modifier(text, match.start())
            if modifier == 'prefer':
                result.unresolved.append('Для посадки и материала пока укажите обязательное условие')
                continue
            target = result.excluded_features if modifier == 'exclude' else result.required_features
            target.setdefault(dimension, [])
            if value not in target[dimension]:
                target[dimension].append(value)
    for style, pattern in {'casual': r'повседневн\w*|casual',
            'business': r'классическ\w*|делов\w*', 'minimalism': r'минимализм|минималистич\w*'}.items():
        for match in _matches(pattern, text):
            consume(match)
            if _modifier(text, match.start()) == 'exclude':
                result.excluded_features.setdefault('style', []).append(style)
            else:
                append('preferred_styles', style)
    for match in _matches(r'дешевле|слишком\s+дорого', text):
        consume(match)
        caps = [as_rubles(x) for x in (result.budget_max, default_budget, reference_price) if x and x > 0]
        if not previous or not caps:
            result.unresolved.append('Сначала найдите вещь, чтобы выбрать дешевле')
        else:
            result.budget_max = max(1, int(min(caps) * Decimal('0.8')))
    for match in _matches(r'(?:другой|другого|иной|иного)\s+цвет(?:а)?', text):
        consume(match)
        colors = set(reference_colors) or set(result.colors)
        if not previous or not colors:
            result.unresolved.append('Укажите цвет, который хотите исключить')
        else:
            result.excluded_colors = sorted(set(result.excluded_colors) | colors)
            result.colors = []
            result.preferred_colors = []
    leftover = ''.join(' ' if consumed[i] else ch for i, ch in enumerate(text))
    unknown = [word for word in re.findall(r'[\w/-]+', leftover) if word not in FILLER]
    if unknown:
        result.unresolved.append('Уточните неподдержанные условия: ' + ' '.join(unknown)[:160])
    if not result.requested_categories and result.intent_type == 'single_item' and not previous:
        result.unresolved.append('Укажите вещь: например, брюки, платье или куртка')
    if result.budget_min is not None and result.budget_max is not None and result.budget_min > result.budget_max:
        result.unresolved.append('Минимальная цена выше максимальной')
    if set(result.colors) & set(result.excluded_colors):
        result.unresolved.append('Один цвет одновременно запрошен и исключён')
    for dim, values in result.required_features.items():
        if set(values) & set(result.excluded_features.get(dim, [])):
            result.unresolved.append('Один признак одновременно запрошен и исключён')
    if len(result.required_features.get('footwear_type', [])) > 1:
        result.unresolved.append('Укажите один вид обуви для этой подборки')
    # Refinements cannot silently approve an unresolved original request.
    if previous and previous.unresolved:
        result.unresolved.extend(previous.unresolved)
    result.unresolved = list(dict.fromkeys(result.unresolved))
    return result


def replacement_slot(text):
    """An explicit replacement action, rather than a new taste constraint."""
    roles = {'верх': 'top', 'низ': 'bottom', 'брюки': 'bottom', 'обувь': 'shoes', 'платье': 'one_piece'}
    match = re.fullmatch(r'(?:оставь(?:те)? (верх|низ|обувь|платье)[ ,]+)?(?:поменяй(?:те)?|замени(?:те)?) (верх|низ|брюки|обувь|платье)[.!]?',
        ' '.join(text.lower().replace('ё', 'е').split()))
    if not match or match[1] and roles[match[1]] == roles[match[2]]:
        return None
    return roles[match[2]]
