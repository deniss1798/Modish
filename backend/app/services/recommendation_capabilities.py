"""Authoritative advertised recommendation capabilities across onboarding."""
from ..config import style_visual_enabled

_BASE_FEATURES = ('fit_sizes_v1', 'shopping_intent_v1', 'catalogue_discovery_v1')
_ACTIVATION_FEATURES = ('context_v1', 'dna_ranking_v1', 'personalized_search_v1')


def recommendation_features(*, activation=False):
    # Return fresh value data so a response/caller cannot mutate the contract.
    features = list(_BASE_FEATURES + (_ACTIVATION_FEATURES if activation else ()))
    if style_visual_enabled():
        features.append('style_visual_v1')
    return features
