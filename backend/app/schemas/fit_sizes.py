"""Explicit user-provided sizes; no inferred size defaults."""
from pydantic import BaseModel, Field, ConfigDict


class SizeSelection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    value: str | None = Field(default=None, max_length=16)
    system: str | None = Field(default=None, pattern='^(RU|EU|US|UK|LETTER|WAIST)$')


class FitSizesInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    top: SizeSelection | None = None
    bottom: SizeSelection | None = None
    one_piece: SizeSelection | None = None
    shoes: SizeSelection | None = None


class BodyMeasurementsInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    chest_cm: float | None = Field(default=None, ge=30, le=250)
    waist_cm: float | None = Field(default=None, ge=30, le=250)
    hips_cm: float | None = Field(default=None, ge=30, le=250)
    inseam_cm: float | None = Field(default=None, ge=20, le=150)
