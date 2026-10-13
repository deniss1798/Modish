"""Category-specific, confirmed sizes belong to Fit, never to learned Taste."""
from copy import deepcopy
from datetime import datetime, timezone

from .catalog.size_resolver import resolve_size
from .garment_roles import product_role

SLOT_CATEGORIES = {'top': 'рубашки', 'bottom': 'брюки', 'one_piece': 'платья', 'shoes': 'обувь'}
_UNSET = object()


def size_profile_to_api(fit) -> dict:
    stored = (getattr(fit, 'size_profile_json', None) or {}) if fit else {}
    sizes = stored.get('sizes') or {}
    return {'version': 1, 'height_confirmed': bool(stored.get('height_confirmed')),
        'sizes': {slot: deepcopy(sizes.get(slot) or {'value': None, 'system': None, 'state': 'size_unknown'})
                  for slot in SLOT_CATEGORIES}}


def update_size_profile(fit, *, sizes: dict, height_confirmed=False, now=None) -> None:
    """Merge only submitted slots; an explicit null clears a confirmed size."""
    profile = size_profile_to_api(fit)
    timestamp = (now or datetime.now(timezone.utc)).isoformat()
    for slot, choice in sizes.items():
        if slot not in SLOT_CATEGORIES:
            raise ValueError('Unknown size slot: ' + slot)
        choice = choice or {}
        resolved = resolve_size(choice.get('value'), size_system=choice.get('system'), category=SLOT_CATEGORIES[slot])
        profile['sizes'][slot] = {'value': resolved.normalized_size, 'raw_value': resolved.raw_size,
            'system': resolved.size_system, 'state': 'confirmed' if resolved.known else 'size_unknown',
            'reason': resolved.reason, 'confirmed_at': timestamp if resolved.known else None}
    profile['height_confirmed'] = profile['height_confirmed'] or height_confirmed
    fit.size_profile_json = profile


def submitted_sizes(*, clothing_size=_UNSET, clothing_system=None, shoe_size=None, shoe_system=None, overrides=None) -> dict:
    sizes = {} if clothing_size is _UNSET else {
        slot: {'value': clothing_size, 'system': clothing_system} for slot in ('top', 'bottom', 'one_piece')}
    if shoe_size is not None:
        sizes['shoes'] = {'value': shoe_size, 'system': shoe_system}
    sizes.update(overrides or {})
    return sizes


def requested_size_for_product(fit, product) -> tuple[str | None, str | None]:
    slot = product_role(product)
    if slot == 'outerwear':
        slot = 'top'
    if slot not in SLOT_CATEGORIES:
        return None, None
    # Candidate checks only read one confirmed slot. Keep full defensive copies
    # at the API/update boundary, without copying every slot for each garment.
    stored = (getattr(fit, 'size_profile_json', None) or {}) if fit else {}
    selected = (stored.get('sizes') or {}).get(slot) or {}
    if selected.get('state') != 'confirmed':
        return None, None
    values = selected.get('value'), selected.get('system')
    # Valid stored values are immutable strings. Preserve defensive behavior
    # for malformed JSON values too, so callers cannot mutate the profile.
    return values if all(value is None or isinstance(value, str) for value in values) else deepcopy(values)


def missing_fit_requirements(fit) -> list[str]:
    profile = size_profile_to_api(fit)
    missing = [slot for slot, size in profile['sizes'].items() if size.get('state') != 'confirmed']
    if not profile['height_confirmed']:
        missing.append('height')
    return missing
