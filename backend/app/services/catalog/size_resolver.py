"""Lossless size tokens and exact matching, without guessed conversions.

Merchant/brand charts belong at this boundary, scoped to their merchant, brand,
category and chart version. No cross-system conversion is enabled by default.
"""
from dataclasses import asdict, dataclass, replace
import re

from ...catalog_normalize import normalize_category, normalize_size_system, normalize_size_token, split_size_values

RESOLVER_VERSION = 'size_resolver_v2'
LETTER = re.compile(r'XXXS|XXS|XS|S|M|L|XL|XXL|XXXL|[2-6]XL')
_SYSTEM_KEYS = ('size_system', 'param_размер_unit', 'param_size_unit', 'размер_unit',
                'size_unit', 'param_размер_units', 'param_size_units', 'param_size_system')
_SYSTEMS = {'RU', 'EU', 'US', 'UK', 'INT', 'LETTER', 'WAIST'}
_SYSTEM_PREFIX = re.compile(r'(RU|EU|US|UK|INT|LETTER)[:\-]?(.*)')


def _system_values(raw=None, params=None):
    values = []
    for data in (raw, params):
        if not isinstance(data, dict):
            continue
        keys = {str(k).strip().lower().replace(' ', '_'): v for k, v in data.items()}
        for key in _SYSTEM_KEYS:
            if key not in keys:
                continue
            values.extend(keys[key] if isinstance(keys[key], list) else [keys[key]])
    return [normalize_size_system(str(value or '')) for value in values]


def explicit_size_system(raw=None, params=None):
    values = _system_values(raw, params)
    return values[0] if values and values[0] in _SYSTEMS and len(set(values)) == 1 else None


@dataclass(frozen=True)
class ResolvedSize:
    raw_size: str
    normalized_size: str | None
    size_system: str | None
    category: str
    merchant_id: str
    availability: str = 'unknown'
    reason: str | None = None

    @property
    def known(self) -> bool:
        return bool(self.normalized_size and self.size_system and self.reason is None)

    def to_dict(self) -> dict:
        return {**asdict(self), 'resolver_version': RESOLVER_VERSION}


def resolve_size(value, *, size_system=None, category='', merchant_id='', availability='unknown') -> ResolvedSize:
    raw = str(value or '').strip()
    token = normalize_size_token(raw.replace(',', '.'))
    system = normalize_size_system(size_system)
    category = normalize_category(category)
    result = ResolvedSize(raw, None, None, category, str(merchant_id or ''), availability)
    if not token:
        return replace(result, reason='size_unknown')
    prefix = _SYSTEM_PREFIX.fullmatch(token)
    if prefix:
        encoded, token = prefix.groups()
        if system and system != encoded:
            return replace(result, reason='conflicting_size_system')
        system = encoded
    if LETTER.fullmatch(token):
        if system and system not in {'LETTER', 'INT'}:
            return replace(result, reason='conflicting_size_system')
        return replace(result, normalized_size=token, size_system='LETTER')
    if re.fullmatch(r'W\d{2}(?:L\d{2})?', token):
        if system and system != 'WAIST':
            return replace(result, reason='conflicting_size_system')
        if category and category not in {'брюки', 'джинсы', 'шорты'}:
            return replace(result, reason='size_category_mismatch')
        return replace(result, normalized_size=token, size_system='WAIST')
    if re.fullmatch(r'\d{1,2}(?:\.5)?', token):
        reason = None if system in {'RU', 'EU', 'US', 'UK'} else 'size_system_unknown'
        return replace(result, normalized_size=token, size_system=system if not reason else None, reason=reason)
    return replace(result, reason='unrecognized_size')


def _raw_sizes(product):
    raw = getattr(product, 'feed_raw_json', None) or {}
    keys = {str(k).strip().lower().replace(' ', '_'): v for k, v in raw.items()} if isinstance(raw, dict) else {}
    original = next((keys[k] for k in ('sizes', 'size', 'available_sizes', 'param_sizes', 'param_размер', 'param_size')
                     if keys.get(k)), None)
    return split_size_values(original)


def _size_identity(value):
    return normalize_size_token(str(value).replace(',', '.'))


def _flat_sizes_may_match(values, normalized_size):
    if values is None:
        return False
    # Persisted flat sizes are reusable lists. Keep other legacy containers on
    # the full path; probing an iterator would consume its only matching token.
    if not isinstance(values, (list, tuple)):
        return True
    for value in values:
        token = _size_identity(value)
        prefix = _SYSTEM_PREFIX.fullmatch(token)
        if (prefix.group(2) if prefix else token) == normalized_size:
            return True
    return False


def may_match_flat_size(product, requested, *, requested_system=None) -> bool:
    """Cheap negative-only probe; True does not establish size or stock.

    Thin catalogue projections can use this without reading merchant JSON.
    Prefix/conflict/original/per-variant availability proof remains mandatory
    in exact_size_match after the full product has been loaded.
    """
    if not requested:
        return False
    wanted = resolve_size(requested, size_system=requested_system,
        category=product.category or product.category_name or '')
    return wanted.known and _flat_sizes_may_match(product.available_sizes, wanted.normalized_size)


def resolve_product_sizes(product) -> tuple[ResolvedSize, ...]:
    raw = getattr(product, 'feed_raw_json', None)
    params = getattr(product, 'raw_params_json', None)
    system = explicit_size_system(raw, params)
    conflict = bool(_system_values(raw, params)) and system is None
    original_values = {_size_identity(value): str(value) for value in _raw_sizes(product) if value}
    original_tokens = set(original_values)
    compounds = any(any(separator in value for separator in '/-,') for value in original_values.values())
    details = getattr(product, 'available_sizes_detailed', None) or []
    out = []
    for value in product.available_sizes or []:
        raw_value = original_values.get(_size_identity(value), value)
        size = resolve_size(raw_value, size_system=system,
            category=product.category or product.category_name or '',
            merchant_id=getattr(product, 'source_id', None) or product.source or '', availability='available')
        if conflict:
            size = replace(size, reason='conflicting_size_system')
        elif compounds and _size_identity(value) not in original_tokens:
            size = replace(size, reason='ambiguous_compound_size')
        matching_details = [d for d in details if isinstance(d, dict)
            and _size_identity(d.get('raw_size', d.get('size', ''))) == _size_identity(value)]
        if matching_details:
            aliases = {'in_stock': 'available', 'out_of_stock': 'unavailable'}
            states = {aliases.get(state, state) for d in matching_details
                for state in [str(d.get('availability', 'unknown')).strip().lower()]}
            # Contradictory/unknown per-size stock cannot be upgraded by a flat list.
            if states != {'available'}:
                size = replace(size, availability='unavailable' if states == {'unavailable'} else 'unknown')
        out.append(size)
    return tuple(out)


def exact_size_match(product, requested, *, requested_system=None) -> bool:
    if not requested:
        return False
    wanted = resolve_size(requested, size_system=requested_system,
                          category=product.category or product.category_name or '')
    if not wanted.known:
        return False
    # Raw originals replace flat values only at the same _size_identity, and
    # this resolver performs no conversions. A different flat token cannot
    # become the requested value. Possible matches still need every existing
    # unit, compound-token and per-size stock check below.
    if not _flat_sizes_may_match(product.available_sizes, wanted.normalized_size):
        return False
    return any(size.known and size.availability == 'available'
        and size.size_system == wanted.size_system and size.normalized_size == wanted.normalized_size
        for size in resolve_product_sizes(product))
