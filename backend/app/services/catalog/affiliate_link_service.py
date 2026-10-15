from __future__ import annotations

import ipaddress
import re
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit, unquote

from ..public_fetch import _public_address

if TYPE_CHECKING:
  from ...models import Product, ProductSource


def _public_web_url(raw: str) -> str:
  """Check web syntax without DNS/network calls during candidate admission.

  This is not a reachability check. Server-side downloads separately resolve
  and pin every public address through public_fetch before opening a socket.
  """
  if not isinstance(raw, str):
    return ""
  url = raw.strip()
  if len(url) > 8192 or '\\' in url or any(ord(c) <= 32 or ord(c) == 127 for c in url):
    return ""
  try:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if host.endswith('.'):
      host = host[:-1]
    port = parsed.port  # Access validates malformed/out-of-range ports too.
    if (parsed.scheme not in {"https", "http"} or not host
        or parsed.username is not None or parsed.password is not None
        or port == 0 or '%' in host):
      return ""
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
      return ""
    try:
      ipaddress.ip_address(host)
      if not _public_address(host):
        return ""
    except ValueError:
      ascii_host = host.encode('idna').decode('ascii')
      labels = ascii_host.split('.')
      if (len(ascii_host) > 253 or len(labels) < 2
          or ascii_host == 'localhost'
          or ascii_host.endswith(('.localhost', '.local', '.internal'))
          or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                 for label in labels)
          # Browsers may interpret these as IPv4 shorthand/octal/hex literals.
          or all(re.fullmatch(r'(?:[0-9]+|0x[0-9a-f]+)', label) for label in labels)):
        return ""
    return url
  except (ValueError, UnicodeError):
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
