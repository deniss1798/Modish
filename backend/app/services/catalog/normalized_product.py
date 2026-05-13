from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class NormalizedProduct:
  """Единый формат после парсинга любого affiliate feed."""
  external_id: str
  source_code: str
  title: str
  brand: str
  category: str
  subcategory: str | None
  price: int
  old_price: int | None
  currency: str
  image_url: str
  original_url: str
  affiliate_url: str | None
  sizes: list[str] = field(default_factory=list)
  colors: list[str] = field(default_factory=list)
  gender_target: str | None = None
  material: str | None = None
  season: str | None = None
  style_tags: list[str] = field(default_factory=list)
  raw_data: dict[str, Any] = field(default_factory=dict)
  discount_percent: int | None = None
  availability_status: str | None = None
  merchant_category: str | None = None
  merchant_subcategory: str | None = None
  group_id: str | None = None
  description: str | None = None
  image_urls: list[str] = field(default_factory=list)
  barcode: str | None = None
  vendor_code: str | None = None
  category_external_id: str | None = None
  category_name: str | None = None
  raw_params: dict[str, Any] = field(default_factory=dict)
  size_original: str | None = None
  color_original: str | None = None
