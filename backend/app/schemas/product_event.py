"""Shared feedback payload for HTTP and onboarding workflows."""
from typing import Any

from pydantic import BaseModel


class EventRequest(BaseModel):
  event_type: str
  product_id: str | None = None
  outfit_id: str | None = None
  meta: dict[str, Any] | None = None
