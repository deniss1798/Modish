import unittest
from types import SimpleNamespace

from app.services.feed_filters import (
  product_passes_budget,
  product_passes_size,
)


class FeedFiltersTests(unittest.TestCase):
  def test_budget_hard_cap(self) -> None:
    fit = SimpleNamespace(budget_max=5000)
    p_ok = SimpleNamespace(price=4500)
    p_bad = SimpleNamespace(price=15000)
    self.assertTrue(product_passes_budget(p_ok, fit))
    self.assertFalse(product_passes_budget(p_bad, fit))

  def test_size_xl_not_s_only(self) -> None:
    fit = SimpleNamespace(clothing_size="XL")
    p_s_only = SimpleNamespace(available_sizes=["S", "M"])
    p_has_xl = SimpleNamespace(available_sizes=["M", "XL"])
    self.assertFalse(product_passes_size(p_s_only, fit))
    self.assertTrue(product_passes_size(p_has_xl, fit))


if __name__ == "__main__":
  unittest.main()
