"""Read-only exposure-aware brand and category price evidence from real actions.

A feed delivery is not an exposure. Only a visible view or an explicit action
establishes exposure; repeated events for the same product/day do not inflate it.
Price history uses server-recorded event snapshots, never today's changed price.
"""
from datetime import datetime, timedelta, timezone
from math import exp
from sqlalchemy import select
from ..models import Product, RecommendationEventV2
from ..money import as_rubles
from ..catalog_normalize import normalize_category

POSITIVE = {'like': 1.0, 'save': 1.0, 'open_product': .3, 'affiliate_click': 1.0,
    'purchase': 1.0, 'post_purchase_positive': 1.0}
EXPOSURE_EVENTS = {'view', 'skip', 'dislike', 'unsave', 'post_purchase_negative', *POSITIVE}


def aggregate_behavior(rows):
    exposures, prices = {}, {}
    for product_id, current_brand, category, event, metadata, occurred_at in rows:
        if event not in EXPOSURE_EVENTS:
            continue
        metadata = metadata or {}
        brand = str(metadata.get('observed_brand', current_brand) or '').strip().lower()
        category = normalize_category(str(metadata.get('observed_category', category) or ''))
        key = (brand, product_id, occurred_at.date().isoformat())
        item = exposures.setdefault(key, {'positive': 0.0, 'negative': 0.0})
        reason = metadata.get('reason')
        item['positive'] = max(item['positive'], POSITIVE.get(event, 0))
        negative = 0.0
        if event == 'skip': negative = .1
        elif event in {'dislike', 'post_purchase_negative', 'unsave'} and reason not in {
                'too_expensive', 'not_for_context', 'dont_like_color', 'dont_like_fit',
                'dont_like_category', 'not_my_style', 'dont_like_design', 'already_have_similar'}:
            negative = 1.0 if event != 'unsave' else .3
        item['negative'] = max(item['negative'], negative)
        price = metadata.get('observed_price')
        if category and isinstance(price, (int, float)) and not isinstance(price, bool) and price > 0:
            sample_key = (category, product_id, occurred_at.date().isoformat())
            sample = prices.setdefault(sample_key, {'price': price, 'weight': 0, 'too_expensive': False})
            # Query order is newest first; preserve the newest observed price.
            sample['weight'] = max(sample['weight'], POSITIVE.get(event, 0))
            sample['too_expensive'] |= event in {'dislike', 'post_purchase_negative'} and reason == 'too_expensive'
    brands = {}
    for (brand, _, _), signals in exposures.items():
        if not brand: continue
        data = brands.setdefault(brand, {'exposure_count': 0, 'positive': 0.0, 'negative': 0.0})
        data['exposure_count'] += 1
        data['positive'] += signals['positive']; data['negative'] += signals['negative']
    for data in brands.values():
        n, positive, negative = data['exposure_count'], data['positive'], data['negative']
        active = positive + negative
        consistency = abs(positive - negative) / active if active else 0
        data['confidence'] = (1-exp(-active/6)) * (.55 + .45*consistency)
        data['affinity'] = max(0.0, ((positive+1)/(n+5)-.2)/.8)
        data['negative_affinity'] = min(1.0, negative/(n+3))
    categories = {}
    for (category, _, _), value in prices.items():
        categories.setdefault(category, []).append(value)
    price_profile = {}
    for category, samples in categories.items():
        positive = sorted((s['price'], s['weight']) for s in samples[:200] if s['weight'] > 0)
        rejected = sorted(s['price'] for s in samples[:200] if s['too_expensive'])
        def quantile(q):
            target = sum(weight for _, weight in positive)*q
            running = 0
            for price, weight in positive:
                running += weight
                if running >= target: return price
            return None
        enough = len(positive) >= 3
        price_profile[category] = {'sample_count': len(positive),
            'confidence': min(1.0, len(positive)/10),
            'preferred_min': quantile(.2) if enough else None,
            'preferred_max': quantile(.8) if enough else None,
            'rejected_price_median': rejected[len(rejected)//2] if rejected else None}
    return {'version': 1, 'basis': 'unique_product_day_visible_actions_last_90_days',
        'brands': brands, 'category_prices': price_profile}


def load_behavior_profile(db, *, user_id, as_of=None):
    now = as_of or datetime.now(timezone.utc)
    rows = db.execute(select(RecommendationEventV2.product_id, Product.brand, Product.category,
        RecommendationEventV2.event_type, RecommendationEventV2.meta_json, RecommendationEventV2.created_at)
        .join(Product, Product.id == RecommendationEventV2.product_id)
        .where(RecommendationEventV2.user_id == user_id,
            RecommendationEventV2.created_at >= now-timedelta(days=90),
            RecommendationEventV2.event_type.in_(EXPOSURE_EVENTS))
        .order_by(RecommendationEventV2.created_at.desc())
        .execution_options(yield_per=500))
    return aggregate_behavior(rows)


def behavior_adjustment(product, snapshot):
    brands = (snapshot or {}).get('brands', {})
    brand = brands.get(str(product.brand or '').strip().lower())
    score = 0.0
    if brand:
        score += .07 * (brand['affinity']-brand['negative_affinity']) * brand['confidence']
    return score + category_price_adjustment(product, snapshot)


def category_price_adjustment(product, snapshot):
    score = 0.0
    price = (snapshot or {}).get('category_prices', {}).get(normalize_category(product.category))
    if price and price['preferred_min'] is not None and price['preferred_max'] is not None:
        within = as_rubles(price['preferred_min']) <= as_rubles(product.price) <= as_rubles(price['preferred_max'])
        score += .04 * price['confidence'] * (1 if within else -.5)
    return score
