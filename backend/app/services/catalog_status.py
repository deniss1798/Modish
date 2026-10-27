"""Global stock-observation diagnostics, never personalized product admission."""
from datetime import datetime, timezone

from sqlalchemy import select

from ..models import Product
from .catalog.stock_freshness import available_product_conditions


_MESSAGES = {
    'ready': 'В каталоге есть актуальные сведения о наличии. Подбор зависит от ваших параметров.',
    'refresh_required': (
        'Данные о наличии устарели. Каталогу нужно обновление.'
    ),
    'empty': 'Сейчас в каталоге нет доступных предложений.',
}


def catalogue_status(db, *, checked_at=None):
    """Use one snapshot and two EXISTS probes, without loading products/counts.

    Ready means at least one active, reported-in-stock non-demo offer has a
    fresh observation. Adult/fashion, exact size, budget, source rules and
    personal history still apply separately; no recommendation is promised.
    """
    now = checked_at or datetime.now(timezone.utc)
    now = (now.replace(tzinfo=timezone.utc) if now.tzinfo is None
           else now.astimezone(timezone.utc))
    base = (
        Product.is_active == 1,
        Product.is_available == 1,
        Product.is_deleted_from_feed == 0,
        Product.source != 'demo',
        Product.availability_status.in_(['in_stock', 'available']),
    )
    fresh = select(Product.id).where(
        *available_product_conditions(checked_at=now), Product.source != 'demo',
    ).exists()
    reported = select(Product.id).where(*base).exists()
    # This diagnostic must not flush unrelated pending caller changes.
    with db.no_autoflush:
        has_fresh, has_reported = db.execute(select(fresh, reported)).one()
    status = 'ready' if has_fresh else 'refresh_required' if has_reported else 'empty'
    return {
        'status': status,
        'scope': 'global',
        'personalized': False,
        'checked_at': now.isoformat(),
        'message': _MESSAGES[status],
    }
