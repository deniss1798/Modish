"""Stable identities shared by offers of the same visible catalogue item."""
from urllib.parse import urlsplit

from ..models import Product


def identity_url(raw: str | None) -> str:
  value = (raw or "").strip()
  if not value:
    return ""
  url = urlsplit("https:" + value if value.startswith("//") else value)
  if not url.hostname or not url.path.strip("/"):
    return value
  # CDN resize/tracking queries do not change an image stored at a file path.
  return f"{url.hostname.lower()}{url.path}" + (f"?{url.query}" if url.query and "." not in url.path.rsplit("/", 1)[-1] else "")


def product_identity_keys(product: Product) -> set[str]:
  keys = {f"id:{product.id}"}
  source = (product.source or "").strip().lower()
  for field in ("group_id", "vendor_code"):
    value = str(getattr(product, field, None) or "").strip()
    if source and value and value.lower() not in {"0", "none", "null", "unknown"}:
      keys.add(f"{field}:{source}:{value}")
  image = identity_url(product.image_url)
  if image:
    keys.add(f"image:{image}")
  return keys
