"""Bounded footwear nouns shared by intent parsing and exact item admission.

The catalogue category ``обувь`` is intentionally broad. A request for pumps
must retain its narrower meaning instead of admitting sandals or trainers.
Only sold-item titles, merchant type attributes and category labels establish
a type; descriptions and the other garments in a photograph do not.
"""
import re


FOOTWEAR_PATTERNS = {
    'pumps': r'туфл(?:и|я|ю|е|ей|ям|ями|ях)|туфель|лодочк(?:и|а|у|е|ой|ам|ами|ах)|лодочек|pumps?',
    'loafers': r'лофер(?:ы|а|ов|ам|ами|ах)?|loafers?',
    'moccasins': r'мокасин(?:ы|а|ов|ам|ами|ах)?|moccasins?',
    'sneakers': r'кроссов(?:ки|ок|ка|ку|ке|кой|кам|ками|ках)|сникерс?(?:ы|ов|ам|ами|ах)?|sneakers?|trainers?',
    'canvas_shoes': r'кед(?:ы|а|ов|ам|ами|ах)?',
    'ankle_boots': r'ботильон(?:ы|а|ов|ам|ами|ах)?|ankle[ -]boots?',
    'boots': r'ботин(?:ки|ок|ка|ку|ке|ком|кам|ками|ках)|boots?',
    'tall_boots': r'сапог(?:и|а|у|е|ом|ам|ами|ах)?|ботфорт(?:ы|а|ов|ам|ами|ах)?',
    'sandals': r'сандали(?:и|й|я|ю|ей|ям|ями|ях)|sandals?',
    'dress_sandals': r'босонож(?:ки|ек|ка|ку|ке|кой|кам|ками|ках)',
    'ballet_flats': r'балет(?:ки|ок|ка|ку|ке|кой|кам|ками|ках)|ballet[ -]flats?',
    'mules': r'мюли|мюлей|mules?',
    'clogs': r'сабо|clogs?',
    'slip_ons': r'слипон(?:ы|а|ов|ам|ами|ах)?|slip[ -]ons?',
    'espadrilles': r'эспадриль(?:и|я|ю|е|ей|ям|ями|ях)|espadrilles?',
}
FOOTWEAR_RE = {kind: re.compile(r'(?<!\w)(?:' + pattern + r')(?!\w)')
    for kind, pattern in FOOTWEAR_PATTERNS.items()}
FOOTWEAR_SEARCH_TERMS = {
    'pumps': ('туфл', 'туфель', 'лодочк', 'лодочек', 'pump'),
    'loafers': ('лофер', 'loafer'), 'moccasins': ('мокасин', 'moccasin'),
    'sneakers': ('кроссов', 'сникер', 'sneaker', 'trainer'),
    'canvas_shoes': ('кед',), 'ankle_boots': ('ботильон', 'ankle boot', 'ankle-boot'),
    'boots': ('ботин', 'boot'), 'tall_boots': ('сапог', 'ботфорт'),
    'sandals': ('сандал', 'sandal'), 'dress_sandals': ('босонож',),
    'ballet_flats': ('балетк', 'ballet flat', 'ballet-flat'),
    'mules': ('мюли', 'мюлей', 'mule'), 'clogs': ('сабо', 'clog'),
    'slip_ons': ('слипон', 'slip-on', 'slip on'),
    'espadrilles': ('эспадриль', 'espadrille'),
}
_REFERENCE = re.compile(r'(?<!\w)(?:для|к|под|с|со|сочетайт\w*|дополнит\w*|рекомендуем|for|with|pair)(?!\w)')
_TYPE_KEYS = {'тип_обуви', 'вид_обуви', 'shoe_type', 'footwear_type'}


def footwear_types(text):
    """Return explicit nouns, never infer a shoe type from style or heel."""
    text = str(text or '').lower().replace('ё', 'е').replace('_', ' ')
    found = {kind for kind, pattern in FOOTWEAR_RE.items() if pattern.search(text)}
    # ``ankle boots`` contains the generic English ``boots`` token too.
    if 'ankle_boots' in found and re.search(r'\bankle[ -]boots?\b', text):
        found.discard('boots')
    return found


def product_footwear_types(product):
    """A conflicting or absent merchant classification stays unknown."""
    evidence = []
    title = str(getattr(product, 'title', '') or '').lower().replace('ё', 'е')
    title = _REFERENCE.split(re.split(r'[\n;.!?]', title, maxsplit=1)[0], maxsplit=1)[0]
    values = footwear_types(title)
    if len(values) > 1:
        return set()
    if values:
        evidence.append(values)
    for field in ('category', 'category_name', 'subcategory', 'merchant_category'):
        values = footwear_types(getattr(product, field, None))
        if values:
            evidence.append(values)
    for field in ('feed_raw_json', 'raw_params_json'):
        raw = getattr(product, field, None)
        if not isinstance(raw, dict):
            continue
        for key, value in raw.items():
            key = str(key).lower().replace(' ', '_').removeprefix('param_')
            if key in _TYPE_KEYS:
                values = footwear_types(value)
                if values:
                    if len(values) != 1:
                        return set()
                    evidence.append(values)
    # A broad navigation list may contain the explicit sold noun, but cannot
    # prove one of its members by itself. Narrow contradictory labels reject.
    if not evidence or not any(len(values) == 1 for values in evidence):
        return set()
    kinds = set.intersection(*evidence)
    return kinds if len(kinds) == 1 else set()
