"""Short reasons built from confirmed Fit, request constraints and evidence."""
from .fit_size_profile import requested_size_for_product
from .catalog.product_dna import RULE_VERSION
from .personal_size_gate import evaluate_personal_size
from .catalog.stock_freshness import availability_snapshot

FEATURE_LABELS = {'straight': 'прямой крой', 'wide': 'широкий крой', 'slim': 'приталенную посадку',
    'regular': 'стандартную посадку', 'oversized': 'оверсайз', 'cotton': 'хлопок',
    'linen': 'лён', 'wool': 'шерсть', 'silk': 'шёлк', 'viscose': 'вискозу'}


def product_reasons(product, fit, spec, *, facts, taste_features=None, behavior=None):
    if not evaluate_personal_size(product, fit).eligible or not availability_snapshot(product).available:
        return []
    size, system = requested_size_for_product(fit, product)
    maximum = min(fit.budget_max, spec.budget_max or fit.budget_max)
    reasons = [f'Размер {system} {size} указан в наличии', f'Цена в пределах {maximum:,} ₽'.replace(',', ' ')]
    if spec.colors or spec.excluded_colors:
        reasons.append('Цвет соответствует вашему запросу')
    # Construction preferences are shared canonical DNA facts. Unknown legacy
    # strings or a wish in this one request cannot become behavioural proof.
    for dimension in ('silhouette', 'fit', 'material'):
        for value in sorted(facts.get(dimension, set())):
            preference, confidence = (taste_features or {}).get((dimension, RULE_VERSION+':'+value), (0, 0))
            if preference > .35 and confidence >= .4 and value in FEATURE_LABELS:
                reasons.append('В прошлых действиях вы чаще выбирали ' + FEATURE_LABELS[value])
                return reasons[:2] + [reasons[-1]]
    brand = (behavior or {}).get('brands', {}).get(str(product.brand or '').lower())
    if brand and brand['positive'] >= 3 and brand['affinity'] >= .2 and brand['confidence'] >= .3:
        reasons.append('Вещи этого бренда чаще получали ваши положительные отклики')
    return reasons[:3]
