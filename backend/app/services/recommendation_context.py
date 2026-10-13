"""Request-scoped shopping intent. Never writes to persistent taste/profile."""
from dataclasses import dataclass

from .catalog.product_dna import reliable_taste_features
from .garment_roles import product_role
from .outfit_quality import is_sports_item, scenario_product_ok

CONTEXT_STYLES = {
    'daily': {'casual', 'minimalism', 'streetwear'},
    'office': {'business', 'smart_casual', 'minimalism'},
    'date': {'romantic', 'elegant', 'smart_casual'},
    'evening': {'elegant', 'romantic'},
    'restaurant': {'elegant', 'smart_casual'},
    'wedding': {'elegant', 'romantic'},
    'party': {'elegant', 'streetwear'},
    'vacation': {'casual', 'romantic'},
    'walk': {'casual', 'streetwear'},
}
OCCASION = {'office': 'work', 'restaurant': 'evening', 'party': 'evening', 'daily': 'walk'}
FORMAL_CONTEXTS = {'office', 'date', 'evening', 'restaurant', 'wedding', 'party'}
CONTEXT_FORMALITY = {
    'daily': {'casual'}, 'office': {'smart', 'formal'},
    'date': {'smart'}, 'evening': {'smart', 'formal'},
    'restaurant': {'smart', 'formal'}, 'wedding': {'formal'},
    'party': {'smart', 'formal'}, 'vacation': {'casual'}, 'walk': {'casual'},
}


@dataclass(frozen=True)
class ShoppingContext:
    scenario: str = 'daily'
    season: str | None = None
    budget_max: int | None = None

    def __post_init__(self):
        if self.scenario not in CONTEXT_STYLES:
            raise ValueError('Unsupported shopping context')
        if self.season not in {None, 'winter', 'summer', 'demiseason', 'all_season'}:
            raise ValueError('Unsupported season')
        if self.budget_max is not None and self.budget_max < 0:
            raise ValueError('Negative purchase budget')

    def allows(self, product, *, semantics=None):
        if self.budget_max is not None and product.price > self.budget_max:
            return False
        if self.scenario in FORMAL_CONTEXTS:
            if product_role(product) == 'outerwear':
                if is_sports_item(product):
                    return False
            elif not scenario_product_ok(product, self.scenario):
                return False
        if self.season:
            facts = semantics if semantics is not None else reliable_taste_features(product, dimensions=('season',))
            seasons = facts.get('season', set())
            if self.season == 'winter' and 'summer' in seasons or self.season == 'summer' and 'winter' in seasons:
                return False
        return True

    def score(self, product, *, semantics=None):
        facts = semantics if semantics is not None else reliable_taste_features(product, dimensions=('style', 'occasion', 'season', 'formality'))
        if not self.allows(product, semantics=facts):
            return 0.0, []
        score, reasons = 0.5, []
        wanted = OCCASION.get(self.scenario, self.scenario)
        if wanted in facts.get('occasion', set()):
            score += .3
            reasons.append('В характеристиках указан выбранный повод')
        if CONTEXT_STYLES[self.scenario] & facts.get('style', set()):
            score += .15
            reasons.append('Стиль вещи подходит к выбранному случаю')
        if CONTEXT_FORMALITY[self.scenario] & facts.get('formality', set()):
            score += .1
            reasons.append('Уровень формальности подходит к выбранному случаю')
        if self.season and ({self.season, 'all_season'} & facts.get('season', set())):
            score += .05
        return min(1.0, score), reasons


def context_for(scenario='daily', *, season=None, budget_max=None):
    # Preserve existing clients' old general-feed aliases; unknown values must
    # not silently impose unrelated constraints or create a permanent preference.
    scenario = {'casual': 'daily', 'minimal': 'daily'}.get(scenario, scenario)
    return ShoppingContext(scenario if scenario in CONTEXT_STYLES else 'daily', season, budget_max)
