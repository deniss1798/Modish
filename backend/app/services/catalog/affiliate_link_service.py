from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
  from ...models import Product, ProductSource


def apply_deeplink_for_product(
  base_url: str,
  source: ProductSource | None,
  *,
  external_id: str = "",
  original_url: str = "",
) -> str:
  """Подстановка {url}, {affiliate_url}, {original_url}, {external_id} в шаблон источника."""
  base = (base_url or "").strip()
  if not source or not (source.deeplink_template or "").strip():
    return base
  orig = (original_url or "").strip() or base
  ext = (external_id or "").strip()
  tpl = source.deeplink_template.strip()
  return (
    tpl.replace("{affiliate_url}", base)
    .replace("{url}", base)
    .replace("{original_url}", orig)
    .replace("{external_id}", ext)
  )


def resolve_outbound_url(product: Product, source: ProductSource | None = None) -> str:
  """
  URL для перехода: готовый affiliate_url (уже с шаблоном при импорте) > product_url.
  Шаблон применяется только если affiliate_url пуст, а в фиде был базовый url (старые строки).
  """
  au = (product.affiliate_url or "").strip()
  if au:
    return au
  pu = (product.product_url or "").strip()
  ou = (product.original_url or "").strip()
  base = pu or ou
  if base and source and (source.deeplink_template or "").strip():
    return (
      apply_deeplink_for_product(
        base,
        source,
        external_id=product.external_id or "",
        original_url=ou,
      )
      or base
    )
  return pu or ou
