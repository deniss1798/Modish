from __future__ import annotations

import ipaddress
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit, unquote

if TYPE_CHECKING:
  from ...models import Product, ProductSource


def _public_web_url(raw: str) -> str:
  url = str(raw or "").strip()
  try:
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if parsed.scheme not in {"https", "http"} or not host or parsed.username or parsed.password:
      return ""
    if host == "localhost" or "." not in host or host.endswith((".local", ".internal")):
      return ""
    try:
      if not ipaddress.ip_address(host).is_global:
        return ""
    except ValueError:
      pass
    return url
  except ValueError:
    return ""


def merchant_url(raw: str) -> str:
  """Recover a merchant destination instead of opening expired tracking links."""
  url = _public_web_url(raw)
  for _ in range(4):
    if not url:
      return ""
    parsed = urlsplit(url)
    params = parse_qs(parsed.query)
    nested = next((params[k][0] for k in ("ulp", "url", "redirect_url", "destination", "deeplink") if params.get(k)), None)
    if not nested:
      host = (parsed.hostname or "").lower()
      if parsed.path.startswith("/g/") or any(x in host for x in ("admitad", "prf.hn", "goto.", "click.", "clk.")):
        return ""
      return url
    if not nested.startswith(("https://", "http://")):
      nested = unquote(nested)
    url = _public_web_url(nested)
  return ""


def apply_deeplink_for_product(base_url: str, source: ProductSource | None, *, external_id: str = "", original_url: str = "") -> str:
  base = (base_url or "").strip()
  if not source or not (source.deeplink_template or "").strip():
    return base
  original = (original_url or "").strip() or base
  return (source.deeplink_template.strip()
    .replace("{affiliate_url}", base).replace("{url}", base)
    .replace("{original_url}", original).replace("{external_id}", (external_id or "").strip()))


def resolve_outbound_url(product: Product, source: ProductSource | None = None) -> str:
  for field in ("original_url", "product_url", "affiliate_url"):
    url = merchant_url(getattr(product, field, "") or "")
    if url:
      return url
  return ""
