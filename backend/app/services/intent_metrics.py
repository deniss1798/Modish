"""Task telemetry with separate intent, product and outfit denominators.

Generation is delivery, never an impression. Exposures require a visible view
or an explicit action. Raw requests, measurements and merchant credentials are
not metric metadata. Legacy event names remain readable, not dual-written.
"""
from collections import Counter
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from sqlalchemy import select
from ..models import MetricEvent, ShoppingIntent, RecommendationEventV2

EVENT_NAMES = {
    'created': 'intent_created', 'updated': 'intent_updated', 'refined': 'intent_updated',
    'completed': 'intent_completed', 'abandoned': 'intent_abandoned',
    'recommendation_explained': 'recommendation_explained',
    'outfit_generated': 'outfit_generated', 'outfit_saved': 'outfit_saved',
    'outfit_item_replaced': 'outfit_item_replaced', 'size_filtered': 'size_filtered',
    'availability_filtered': 'availability_filtered', 'brand_affinity_updated': 'brand_affinity_updated',
    'reason_dislike': 'reason_dislike', 'parsed': 'intent_parsed',
    'feedback': 'intent_feedback', 'results_generated': 'intent_results_generated',
    'product_viewed': 'intent_product_viewed',
}
LEGACY_NAMES = {'intent_refined': 'intent_updated',
    **{'intent_'+name: name for name in ('outfit_saved', 'outfit_item_replaced')}}
ALLOWED_KEYS = {'intent_id', 'outfit_id', 'product_id', 'version', 'intent_type',
    'category_count', 'has_budget', 'unresolved_count', 'action', 'reason',
    'catalog_count', 'available_count', 'size_checked', 'size_passed',
    'candidate_count_after_stock_budget', 'eligible_count', 'returned_count',
    'invalid_returned_count', 'outfit_count', 'empty', 'parser_version'}


def emit(db, user_id, name, **metadata):
    if name not in EVENT_NAMES:
        raise ValueError('Unsupported metric event')
    if set(metadata)-ALLOWED_KEYS:
        raise ValueError('Unsupported metric field')
    db.add(MetricEvent(id=str(uuid4()), user_id=user_id, name=EVENT_NAMES[name],
        meta_json=metadata, created_at=datetime.now(timezone.utc)))


def _ratio(numerator, denominator):
    return round(numerator / denominator, 4) if denominator else None


def summary(db, *, days=7):
    days = max(1, min(90, days))
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    cohort = {}
    owners = {}
    for intent_id, user_id, context in db.execute(select(ShoppingIntent.id,
            ShoppingIntent.user_id, ShoppingIntent.context_json).where(
            ShoppingIntent.created_at >= since, ShoppingIntent.created_at <= now)
            .execution_options(yield_per=500)):
        cohort[intent_id] = context or {}
        owners[intent_id] = user_id
    counts, total = Counter(), Counter()
    successful, refined, saved_intents, replaced_intents = set(), set(), set(), set()
    generated_outfits, saved_outfits, replaced_outfits = set(), set(), set()
    reason_disliked_intents, reason_disliked_products = set(), set()
    measured_versions = set()
    names = set(EVENT_NAMES.values()) | set(LEGACY_NAMES)
    for user_id, raw_name, metadata in db.execute(select(MetricEvent.user_id,
            MetricEvent.name, MetricEvent.meta_json).where(
            MetricEvent.created_at >= since, MetricEvent.created_at <= now,
            MetricEvent.name.in_(names)).execution_options(yield_per=500)):
        name = LEGACY_NAMES.get(raw_name, raw_name)
        counts[name] += 1
        metadata = metadata or {}
        intent_id = metadata.get('intent_id')
        owned = intent_id in cohort and owners[intent_id] == user_id
        if owned:
            if name == 'reason_dislike':
                reason_disliked_intents.add(intent_id)
                if metadata.get('product_id'):
                    reason_disliked_products.add((intent_id, metadata['product_id']))
            if name == 'intent_results_generated' and metadata.get('returned_count', 0) > 0:
                successful.add(intent_id)
            if name == 'intent_updated':
                refined.add(intent_id)
            if name == 'outfit_saved':
                saved_intents.add(intent_id)
            if name == 'outfit_item_replaced':
                replaced_intents.add(intent_id)
            outfit_id = metadata.get('outfit_id')
            if outfit_id:
                key = (intent_id, outfit_id)
                if name == 'outfit_generated': generated_outfits.add(key)
                if name == 'outfit_saved': saved_outfits.add(key)
                if name == 'outfit_item_replaced': replaced_outfits.add(key)
        if name == 'intent_results_generated':
            # Replayed responses must not multiply the quality denominator.
            version = (user_id, intent_id, metadata.get('version'))
            if version in measured_versions:
                continue
            measured_versions.add(version)
            for key in ('catalog_count', 'available_count', 'size_checked', 'size_passed',
                    'returned_count', 'invalid_returned_count'):
                total[key] += int(metadata.get(key, 0))
    actions = {name: set() for name in ('view', 'like', 'save', 'dislike', 'open_product', 'affiliate_click')}
    product_actions = {name: set() for name in actions}
    # Task-only refusals remain out of Taste, but they are real negative actions
    # in this request. Union avoids counting a rich RecommendationEvent twice.
    actions['dislike'].update(reason_disliked_intents)
    product_actions['dislike'].update(reason_disliked_products)
    exposed_outfits = (saved_outfits | replaced_outfits) & generated_outfits
    clicked_outfits = set()
    query = select(ShoppingIntent.id, RecommendationEventV2.product_id,
            RecommendationEventV2.outfit_id, RecommendationEventV2.event_type).join(RecommendationEventV2,
        (ShoppingIntent.id == RecommendationEventV2.meta_json['intent_id'].as_string()) &
        (ShoppingIntent.user_id == RecommendationEventV2.user_id)).where(
            ShoppingIntent.created_at >= since, ShoppingIntent.created_at <= now,
            RecommendationEventV2.created_at >= since, RecommendationEventV2.created_at <= now,
            RecommendationEventV2.meta_json['surface'].as_string() == 'shopping_intent')
    for intent_id, product_id, outfit_id, event in db.execute(query.execution_options(yield_per=500)):
        if event not in actions:
            continue
        actions[event].add(intent_id)
        if product_id:
            product_actions[event].add((intent_id, product_id))
        outfit_key = (intent_id, outfit_id)
        if outfit_key in generated_outfits:
            exposed_outfits.add(outfit_key)
            if event == 'affiliate_click': clicked_outfits.add(outfit_key)
    def conversion(ids):
        return _ratio(len(set(ids) & cohort.keys()), len(cohort))
    funnel = {'cohort_intents': len(cohort),
        'basis': 'Unique intents created during the window; explicit actions, no inferred purchases',
        'result_rate': conversion(successful), 'save_rate': conversion(actions['save'] | saved_intents),
        'merchant_click_rate': conversion(actions['affiliate_click']), 'refinement_rate': conversion(refined),
        'like_rate': conversion(actions['like']), 'dislike_rate': conversion(actions['dislike']),
        'open_product_rate': conversion(actions['open_product']),
        # Compatibility keys measure intent conversion. True outfit rates are below.
        'outfit_save_rate': conversion(saved_intents), 'replace_rate': conversion(replaced_intents),
        'completion_rate': conversion(i for i, c in cohort.items() if c.get('closure_outcome') == 'completed'),
        'explicit_abandonment_rate': conversion(i for i, c in cohort.items() if c.get('closure_outcome') == 'abandoned')}
    exposed_products = set().union(*product_actions.values())
    recommendation = {'exposed_products': len(exposed_products),
        'basis': 'Unique intent/product pairs with a visible view or explicit action; delivery is not exposure',
        **{name+'_rate': _ratio(len(product_actions[event]), len(exposed_products))
           for name, event in (('like', 'like'), ('save', 'save'), ('dislike', 'dislike'),
                              ('open_product', 'open_product'), ('affiliate_click', 'affiliate_click'))}}
    outfit = {'generated_outfits': len(generated_outfits), 'exposed_outfits': len(exposed_outfits),
        'basis': 'Unique intent/outfit pairs; a constituent view or explicit outfit action establishes exposure',
        'exposure_rate': _ratio(len(exposed_outfits), len(generated_outfits)),
        'save_rate': _ratio(len(saved_outfits & exposed_outfits), len(exposed_outfits)),
        'merchant_click_rate': _ratio(len(clicked_outfits & exposed_outfits), len(exposed_outfits)),
        'replace_item_rate': _ratio(len(replaced_outfits & exposed_outfits), len(exposed_outfits))}
    return {'days': days, 'events': dict(counts), 'intent_funnel': funnel,
        'recommendation_funnel': recommendation, 'outfit_funnel': outfit,
        'availability_pass_rate': _ratio(total['available_count'], total['catalog_count']),
        'size_pass_rate': _ratio(total['size_passed'], total['size_checked']),
        'invalid_recommendation_rate': _ratio(total['invalid_returned_count'], total['returned_count']),
        'basis': {'availability': 'Catalogue within gender/category scope before stock and price filters',
            'size': 'Actually evaluated candidates after stock/budget/gender SQL; outfit probes may stop at a missing mandatory role',
            'invalid': 'Unique SKU per issued intent version; delivered invalidity, not later merchant stock changes'},
        'totals': dict(total)}
