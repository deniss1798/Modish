from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
  from ...models import Product, ProductSource

_PARTNER_MARKERS = (
  "admitad",
  "ad.admitad",
  "goto.",
  "ulp=",
  "subid",
  "sub_id",
  "affiliate",
  "partner",
  "clk.",
  "click.",
  "utm_source=admitad",
  "prf.hn",
)


def _looks_like_partner_url(url: str) -> bool:
  u = (url or "").strip().lower()
  if not u:
    return False
  return any(m in u for m in _PARTNER_MARKERS)


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
  URL для перехода: только партнёрская ссылка.
  Прямой URL магазина без шаблона/deeplink не отдаём.
  """
  au = (product.affiliate_url or "").strip()
  ou = (product.original_url or "").strip()
  pu = (product.product_url or "").strip()

  if au and (_looks_like_partner_url(au) or (ou and au != ou)):
    return au

  if source and (source.deeplink_template or "").strip():
    base = ou or pu
    if base:
      wrapped = apply_deeplink_for_product(
        base,
        source,
        external_id=product.external_id or "",
        original_url=ou,
      )
      if wrapped:
        return wrapped

  if au:
    return au

  return ""
