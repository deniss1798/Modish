"""Authoritative advertised recommendation capabilities across onboarding."""

_BASE_FEATURES = ('fit_sizes_v1', 'shopping_intent_v1', 'catalogue_discovery_v1')
_ACTIVATION_FEATURES = ('context_v1', 'dna_ranking_v1', 'personalized_search_v1')


def recommendation_features(*, activation=False):
    # Return fresh value data so a response/caller cannot mutate the contract.
    return list(_BASE_FEATURES + (_ACTIVATION_FEATURES if activation else ()))
