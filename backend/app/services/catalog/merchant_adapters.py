"""Source-specific input handling ends here, before ranking or User Twin."""
from copy import deepcopy
from dataclasses import dataclass
import re

from .feed_row_mapper import row_to_normalized
from ...catalog_normalize import normalize_category
from .quality_gate import SUPPORTED_CATEGORIES


def category_from_title(title):
    # Only the leading sold-item phrase, never outfit suggestions after "для/под/с".
    lead = re.split(r"\b(?:для|под|к|с|with|for)\b|[,;]", str(title).lower(), maxsplit=1)[0]
    for word in re.findall(r"[а-яёa-z]+", lead)[:6]:
        category = normalize_category(word)
        if category in SUPPORTED_CATEGORIES:
            return category
    return None


@dataclass(frozen=True)
class MerchantAdapter:
    code: str = "generic"
    brand: str | None = None

    def normalize(self, row, source):
        if not isinstance(row, dict):
            return None
        mapped = deepcopy(row)
        keys = {str(k).strip().lower().replace(" ", "_"): v for k, v in row.items()}
        # Structured merchant facts, never guesses from the photo or brand gender.
        for target, aliases in {
            "material": ("param_состав", "param_материал", "param_material"),
            "season": ("param_сезон", "param_season"),
        }.items():
            if not keys.get(target):
                value = next((keys[k] for k in aliases if keys.get(k)), None)
                if value is not None:
                    mapped[target] = value
        if self.brand and not any(keys.get(k) for k in ("vendor", "brand", "manufacturer")):
            mapped["brand"] = self.brand
        product = row_to_normalized(mapped, source)
        if product:
            # Collection labels (DROP 002, Office, vendor.model) are not garment
            # categories. A title noun is evidence; the store's gender is not.
            if product.category not in SUPPORTED_CATEGORIES:
                title_category = category_from_title(product.title)
                if title_category:
                    product.category = title_category
            product.raw_data = deepcopy(row)
        return product


ADAPTERS = {
    "befree": MerchantAdapter("befree", "Befree"),
    "sela": MerchantAdapter("sela", "Sela"),
    "baon": MerchantAdapter("baon", "BAON"),
}
GENERIC_ADAPTER = MerchantAdapter()


def get_adapter(source_code):
    return ADAPTERS.get(str(source_code).strip().lower(), GENERIC_ADAPTER)
