"""Money contract: decimal RUB in storage, RUB numbers at JSON boundaries.

No multiplication by 100. Float inputs are converted through their decimal text;
prices with more than two decimal places round half up to one kopeck. A lone
comma/dot is decimal; mixed separators require valid three-digit grouping.
"""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
import re
from typing import Any

CENT = Decimal('0.01')
ZERO = Decimal('0.00')
MAX_PRODUCT_PRICE = Decimal('10000000.00')
MAX_STORED_AMOUNT = Decimal('999999999999.99')  # NUMERIC(14, 2)
_SPACE = re.compile(r'[\s\u00a0\u202f]+')
_CURRENCY_SUFFIX = re.compile(r'\s*(?:RUB|RUR|₽)$', re.I)


def _decimal_text(raw: str) -> str | None:
    text = _CURRENCY_SUFFIX.sub('', raw.strip()).strip()
    if not text or len(text) > 128 or text.startswith('-'):
        return None
    if _SPACE.search(text):
        # Whitespace may group thousands, but must not splice arbitrary digits.
        grouped = _SPACE.sub(' ', text)
        if not re.fullmatch(r'\+?\d{1,3}(?: \d{3})+(?:[.,]\d+)?', grouped):
            return None
        text = grouped.replace(' ', '')
    if ',' in text and '.' in text:
        decimal = ',' if text.rfind(',') > text.rfind('.') else '.'
        grouping = '.' if decimal == ',' else ','
        integer, fractional = text.rsplit(decimal, 1)
        if (not fractional.isdigit() or not re.fullmatch(
                r'\+?\d{1,3}(?:' + re.escape(grouping) + r'\d{3})+', integer)):
            return None
        text = integer.replace(grouping, '') + '.' + fractional
    elif text.count(',') > 1 or text.count('.') > 1:
        grouping = ',' if ',' in text else '.'
        if not re.fullmatch(r'\+?\d{1,3}(?:' + re.escape(grouping) + r'\d{3})+', text):
            return None
        text = text.replace(grouping, '')
    else:
        text = text.replace(',', '.')
    # Preserve numeric scientific notation consistently across int/float/str.
    return text if re.fullmatch(r'\+?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?', text) else None


def parse_rubles(raw: Any, *, maximum: Decimal | None = MAX_PRODUCT_PRICE) -> Decimal | None:
    if raw is None or isinstance(raw, bool) or not isinstance(raw, (str, int, float, Decimal)):
        return None
    if isinstance(raw, str):
        text = _decimal_text(raw)
        if text is None:
            return None
    else:
        text = str(raw)
    try:
        amount = Decimal(text)
        bound = min(maximum, MAX_STORED_AMOUNT) if maximum is not None else MAX_STORED_AMOUNT
        if not amount.is_finite() or amount.is_signed() or amount > bound:
            return None
        with localcontext() as context:
            context.prec = 32
            result = amount.quantize(CENT, rounding=ROUND_HALF_UP)
        return result if result <= bound else None
    except (InvalidOperation, ValueError, OverflowError):
        return None


def as_rubles(raw: Any) -> Decimal:
    """Trusted model/budget arithmetic; invalid values fail to nonservable zero."""
    parsed = parse_rubles(raw, maximum=None)
    return parsed if parsed is not None else ZERO


def json_rubles(raw: Any) -> int | float:
    """Retain whole-RUB JSON compatibility and two decimal places when needed."""
    amount = as_rubles(raw)
    return int(amount) if amount == amount.to_integral_value() else float(amount)
