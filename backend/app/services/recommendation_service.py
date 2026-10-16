"""Legacy Recommendation JSON backed only by revalidated catalogue outfits."""
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import select

from ..models import FitProfile, Recommendation, User
from .catalog.rule_filters import load_rules_by_source_id
from .outfit_policy import requested_outfit_count


_SLOTS = ({'top', 'bottom', 'shoes'}, {'one_piece', 'shoes'})
_TITLES = {'daily': 'На каждый день', 'office': 'В офис', 'evening': 'На выход'}


def _unique(values):
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))[:12]


def _legacy_fields(outfit):
    products = outfit['products']
    scenario = outfit['style_direction']
    reasons = ['Размеры вещей указаны в наличии.', f"Стоимость комплекта: {outfit['total_price']} ₽."]
    items = {slot: product['title'] for slot, product in products.items()}
    product_ids = {slot: product['id'] for slot, product in products.items()}
    content = {
        'schema_version': 2, 'outfit_id': outfit['id'], 'product_ids': product_ids,
        'items': items, 'total_price': outfit['total_price'],
        'items_v2': [{
            'product_id': product['id'], 'name': product['title'], 'type': slot,
            'color': ', '.join(product.get('colors') or []), 'reason': reasons[0],
            'price': product['price'], 'url': product['product_url'],
            'image_url': product['image_url'], 'brand': product['brand'],
        } for slot, product in products.items()],
        'why_it_fits': reasons, 'alternatives': [],
    }
    tags = {
        'occasion': [scenario], 'item_types': list(products),
        'styles': _unique(style for product in products.values() for style in product.get('style_tags', [])),
        'colors': _unique(color for product in products.values() for color in product.get('colors', [])),
        'silhouettes': _unique(product.get('silhouette') for product in products.values() if product.get('silhouette')),
    }
    return {'title': _TITLES.get(scenario, 'Персональный образ'),
        'description': 'Комплект из товаров каталога по вашим размерам и бюджету.',
        'content_json': content, 'tags_json': tags}


def persist_legacy_recommendations(db, user_id, outfits):
    """Project a bounded, already validated batch without committing it."""
    if len(outfits) > requested_outfit_count(len(outfits)):
        raise ValueError('Legacy outfit projection exceeds its batch bound')
    now, rows = datetime.now(timezone.utc), []
    for outfit in outfits:
        row = Recommendation(id=str(uuid4()), user_id=user_id, type='outfit', status='active',
            created_at=now, updated_at=now, **_legacy_fields(outfit))
        db.add(row)
        rows.append(row)
    db.flush()
    return rows


def legacy_recommendations_to_api(db, user, rows):
    """Revalidate each issued recipe; historical collections keep their size.

    Unsupported textual templates remain stored but cannot make a personal
    product promise. Validation never rewrites a saved record or its identifiers.
    """
    from ..api.outfits import visible_outfits
    from ..api.serializers import rec_to_dict
    rules, result = load_rules_by_source_id(db), {}
    for row in rows:
        content = row.content_json if isinstance(row.content_json, dict) else {}
        ids = content.get('product_ids')
        if (row.type != 'outfit' or not isinstance(ids, dict) or set(ids) not in _SLOTS
                or any(not isinstance(pid, str) or not pid or len(pid) > 64 for pid in ids.values())):
            continue
        tags = {} if row.tags_json is None else row.tags_json if isinstance(row.tags_json, dict) else None
        if tags is None:
            continue
        occasions = tags.get('occasion')
        if occasions is not None and (not isinstance(occasions, list)
                or any(not isinstance(value, str) or not value or len(value) > 64 for value in occasions)):
            continue
        scenario = occasions[0] if occasions else 'daily'
        reference = content.get('outfit_id')
        if not isinstance(reference, str) or not reference or len(reference) > 64 or reference != reference.strip():
            reference = row.id
        proxy = SimpleNamespace(id=reference, items_json=ids,
            total_price=0, style_direction=scenario, intent_constraints_json=None,
            reason='', score=0, is_saved=0, created_at=row.created_at)
        visible = visible_outfits(db, user, [proxy], rules=rules)
        if not visible:
            continue
        payload = rec_to_dict(row)
        payload.update(_legacy_fields(visible[0]))
        result[row.id] = payload
    return result


def generate_recommendations(db, profile, *, count, scenario):
    """Commit-free compatibility helper; HTTP uses independent rank phases."""
    from ..api.outfits import visible_outfits
    from .outfit_engine_v2 import generate_outfits_v2
    from .recommendation_revision import RecommendationSnapshotChanged
    fit = db.scalar(select(FitProfile).where(FitProfile.user_id == profile.user_id))
    if fit is None or fit.gender_target not in {'menswear', 'womenswear', 'unisex'}:
        return []
    user = db.get(User, profile.user_id)
    rows = generate_outfits_v2(db, user, count=requested_outfit_count(count),
        scenario=scenario, replace_existing=False)
    visible = visible_outfits(db, user, rows)
    if len(visible) != len(rows):
        raise RecommendationSnapshotChanged()
    return persist_legacy_recommendations(db, user.id, visible)

