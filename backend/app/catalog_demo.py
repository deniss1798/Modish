"""Демо-ассеты каталога (картинки без кириллицы в URL)."""
from __future__ import annotations

from hashlib import sha256


def demo_picsum_image_url(*, external_id: str, source: str = "demo") -> str:
  digest = sha256(f"{source}:{external_id}".encode("utf-8")).hexdigest()
  pic_id = 1 + (int(digest[:8], 16) % 999)
  return f"https://picsum.photos/id/{pic_id}/600/800"
