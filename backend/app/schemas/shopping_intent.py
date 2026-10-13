"""Versioned contract for a temporary purchase task, independent of Taste."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class IntentSpec(BaseModel):
    model_config = ConfigDict(extra='forbid')
    parser_version: str = 'ru_rules_v1'
    intent_type: Literal['single_item', 'outfit'] = 'single_item'
    requested_categories: list[str] = Field(default_factory=list)
    excluded_categories: list[str] = Field(default_factory=list)
    occasion: str | None = None
    budget_min: int | None = Field(default=None, ge=0, le=1_000_000)
    budget_max: int | None = Field(default=None, ge=1, le=1_000_000)
    colors: list[str] = Field(default_factory=list)
    preferred_colors: list[str] = Field(default_factory=list)
    excluded_colors: list[str] = Field(default_factory=list)
    required_features: dict[str, list[str]] = Field(default_factory=dict)
    excluded_features: dict[str, list[str]] = Field(default_factory=dict)
    preferred_styles: list[str] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)


class IntentCreateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(min_length=2, max_length=500)
    request_id: str = Field(min_length=16, max_length=64, pattern=r'^[a-zA-Z0-9_-]+$')


class IntentRefineRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(min_length=2, max_length=500)
    expected_version: int = Field(ge=1)
    reference_product_id: str | None = Field(default=None, max_length=36)
    reference_outfit_id: str | None = Field(default=None, max_length=36)


class IntentOutfitAction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_version: int = Field(ge=1)


class IntentOutfitReplace(IntentOutfitAction):
    slot: Literal['top', 'bottom', 'one_piece', 'shoes']


class IntentFeedbackRequest(IntentOutfitAction):
    product_id: str = Field(min_length=1, max_length=36)
    reason: Literal['not_my_style', 'too_expensive', 'dont_like_color', 'dont_like_fit',
        'dont_like_brand', 'already_have_similar', 'not_for_context'] | None = None


class IntentCloseRequest(IntentOutfitAction):
    outcome: Literal['completed', 'abandoned']
