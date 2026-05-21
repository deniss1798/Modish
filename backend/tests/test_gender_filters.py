import unittest
from types import SimpleNamespace

from app.services.catalog.rule_filters import product_gender_compatible


class GenderFilterTests(unittest.TestCase):
  def test_menswear_blocks_dress(self) -> None:
    p = SimpleNamespace(
      gender_target=None,
      title="Платье миди",
      category="платья",
      category_name="Платья",
      merchant_category="",
    )
    self.assertFalse(product_gender_compatible(p, "menswear"))

  def test_menswear_allows_mens_jacket(self) -> None:
    p = SimpleNamespace(
      gender_target="menswear",
      title="Куртка",
      category="верхний_слой",
      category_name="Куртки",
      merchant_category="",
    )
    self.assertTrue(product_gender_compatible(p, "menswear"))

  def test_womenswear_blocks_mens_hint(self) -> None:
    p = SimpleNamespace(
      gender_target=None,
      title="Футболка для мужчин",
      category="футболки",
      category_name="",
      merchant_category="",
    )
    self.assertFalse(product_gender_compatible(p, "womenswear"))

  def test_unisex_user_sees_all(self) -> None:
    p = SimpleNamespace(
      gender_target="womenswear",
      title="Юбка",
      category="юбки",
      category_name="",
      merchant_category="",
    )
    self.assertTrue(product_gender_compatible(p, "unisex"))


if __name__ == "__main__":
  unittest.main()
