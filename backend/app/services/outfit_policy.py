"""Small shared response policy; saved libraries are not recommendation batches."""
from ..money import as_rubles

MAX_RECOMMENDED_OUTFITS = 3
EMPTY_OUTFITS_MESSAGE = 'Пока нет подходящих образов. Попробуйте изменить предпочтения или посмотреть каталог.'


def requested_outfit_count(value):
    return max(1, min(MAX_RECOMMENDED_OUTFITS, int(value)))


def outfit_total_within_budget(total, fit):
    """A complete personal outfit must have a confirmed total budget."""
    maximum = getattr(fit, 'budget_max', None)
    return maximum is not None and as_rubles(maximum) > 0 and 0 < as_rubles(total) <= as_rubles(maximum)
