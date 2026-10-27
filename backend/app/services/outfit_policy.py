"""Small shared response policy; saved libraries are not recommendation batches."""
from ..money import as_rubles

MAX_RECOMMENDED_OUTFITS = 3
EMPTY_OUTFITS_MESSAGE = 'Пока нет подходящих образов. Попробуйте изменить предпочтения или посмотреть каталог.'

# A clothing base is useful when footwear cannot be verified. It never admits
# an unknown shoe size or calls a single top/bottom a wearable outfit.
CLOTHING_BASE_SLOTS = (('top', 'bottom'), ('one_piece',))
CLOTHING_BASE_MESSAGE = 'Обувь не подобрана. Показана основа образа из одежды.'


def is_clothing_base(slots):
    return any(set(slots) == set(option) for option in CLOTHING_BASE_SLOTS)


def outfit_completeness(slots):
    base = is_clothing_base(slots)
    return {'completeness': 'clothing_base' if base else 'complete',
            'missing_slots': ['shoes'] if base else [],
            'completion_message': CLOTHING_BASE_MESSAGE if base else None}


def requested_outfit_count(value):
    return max(1, min(MAX_RECOMMENDED_OUTFITS, int(value)))


def outfit_total_within_budget(total, fit):
    """A complete personal outfit must have a confirmed total budget."""
    maximum = getattr(fit, 'budget_max', None)
    return maximum is not None and as_rubles(maximum) > 0 and 0 < as_rubles(total) <= as_rubles(maximum)
