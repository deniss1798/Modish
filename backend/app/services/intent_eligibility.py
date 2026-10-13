"""Current purchase constraints are admission rules, never permanent dislikes."""
import re
from ..catalog_normalize import normalize_category
from .catalog.product_dna import reliable_taste_features
from .garment_roles import product_role
from .product_eligibility import evaluate_eligibility
from .recommendation_context import context_for


def intent_facts(product):
    facts = reliable_taste_features(product)
    if product_role(product) != 'shoes':
        return facts
    # Only explicit heel evidence. A photo, shoe name or absent attribute cannot
    # prove that footwear has no heel. Conflicting observations remain unknown.
    heel = set()
    for raw in (product.feed_raw_json, product.raw_params_json):
        for key, value in (raw or {}).items():
            if str(key).lower().removeprefix('param_') not in {'heel_height_cm', 'высота_каблука_см', 'высота каблука (см)'}:
                continue
            if re.fullmatch(r'\d+(?:[.,]\d+)?(?:\s*см)?', str(value)):
                height = float(re.sub(r'\s*см$', '', str(value)).replace(',', '.'))
                heel.add('flat' if height == 0 else 'heels')
    title = (product.title or '').lower()
    if re.search(r'\bбез каблук\w*|\bна плоской подошве\b', title):
        heel.add('flat')
    elif re.search(r'\bна каблук\w*|\bна шпильке\b', title):
        heel.add('heels')
    facts['heel'] = heel if len(heel) == 1 else set()
    return facts


def constraint_problem(product, spec, facts):
    category = normalize_category(product.category)
    if category in spec.excluded_categories:
        return 'excluded_category'
    if spec.intent_type == 'single_item' and spec.requested_categories and category not in spec.requested_categories:
        return 'requested_category_mismatch'
    colors = facts.get('color', set())
    if spec.colors and not colors.intersection(spec.colors):
        return 'required_color_unconfirmed'
    if spec.excluded_colors and (not colors or colors.intersection(spec.excluded_colors)):
        return 'excluded_color_or_unknown'
    slot = product_role(product)
    for constraints, required in ((spec.required_features, True), (spec.excluded_features, False)):
        for dimension, wanted in constraints.items():
            if dimension == 'heel' and slot != 'shoes':
                continue
            if dimension == 'silhouette' and slot not in {'top', 'bottom', 'one_piece', 'outerwear'}:
                continue
            if dimension == 'fit' and slot == 'shoes':
                continue
            known = facts.get(dimension, set())
            if required and not set(wanted) <= known:
                return 'required_feature_unconfirmed'
            if not required and (not known or known.intersection(wanted)):
                return 'excluded_feature_or_unknown'
    if spec.occasion and not context_for(spec.occasion).allows(product, semantics=facts):
        return 'occasion_mismatch'
    return None


def intent_commerce_problem(product, fit, spec):
    maximum = min(fit.budget_max, spec.budget_max or fit.budget_max) if fit and fit.budget_max else None
    decision = evaluate_eligibility(product, fit, budget_max=maximum)
    if not decision.eligible:
        return decision.reason
    if spec.budget_min is not None and spec.intent_type == 'single_item' and product.price < spec.budget_min:
        return 'below_requested_budget'
    return None


def intent_product_problem(product, fit, spec, *, facts=None):
    problem=intent_commerce_problem(product,fit,spec)
    if problem: return problem
    return constraint_problem(product, spec, facts if facts is not None else intent_facts(product))
