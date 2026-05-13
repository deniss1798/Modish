"""Демо-ассеты каталога (URL превью без кириллицы; placehold.co вместо picsum для гео/сетей)."""
from __future__ import annotations

from hashlib import sha256


def demo_picsum_image_url(*, external_id: str, source: str = "demo") -> str:
  """
  Демо-превью: placehold.co (часто доступен там, где picsum.photos режут по сети/гео).
  Имя функции оставлено для совместимости с импортами.
  """
  digest = sha256(f"{source}:{external_id}".encode("utf-8")).hexdigest()
  bg = digest[:6]
  fg = digest[6:12]
  return f"https://placehold.co/600x800/{bg}/{fg}/png?text=+"
