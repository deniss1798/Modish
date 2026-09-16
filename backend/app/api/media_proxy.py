"""Прокси изображений для клиентов без исходящего HTTPS (типично Android-эмулятор)."""
from __future__ import annotations

import ipaddress
from urllib.parse import urlparse, urljoin

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

router = APIRouter(prefix="/media", tags=["media"])

_MAX_BYTES = 12 * 1024 * 1024


def _upstream_headers(target: str) -> dict[str, str]:
  ua = (
    "Mozilla/5.0 (Linux; Android 13; Mobile) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
  )
  h: dict[str, str] = {
    "User-Agent": ua,
    "Accept": "image/jpeg,image/png,image/webp,image/apng,image/*,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
  }
  parsed = urlparse(target)
  host = (parsed.hostname or "").lower()
  if "lmcdn" in host or "lamoda" in host:
    h["Referer"] = "https://www.lamoda.ru/"
    h["Origin"] = "https://www.lamoda.ru"
  elif any(x in host for x in ("befree", "mrp.ru", "mr-b.ru", "mrprice")):
    h["Referer"] = "https://befree.ru/"
    h["Origin"] = "https://befree.ru"
  else:
    h["Referer"] = f"{parsed.scheme}://{parsed.netloc}/"
  return h


def _host_allowed_for_proxy(hostname: str) -> bool:
  h = hostname.lower().strip()
  if not h or h == "localhost":
    return False
  # SSRF: не резолвим произвольные имена в IP здесь; режем очевидное.
  if h.startswith("127.") or h.startswith("10.") or h.startswith("192.168."):
    return False
  try:
    ipaddress.ip_address(h)
    return False
  except ValueError:
    pass
  allowed_suffixes = (
    "befree.ru",
    "lamoda.ru",
    "lmcdn.ru",
    "baon.ru", "vipavenue.ru", "sportmaster.ru", "demix.ru",
    "sela.ru", "fablestore.ru", "mongolshop.ru", "tsum.com",
    "serginnetti.ru", "aimclo.ru", "postmeridiem-brand.com", "shoppinglive.ru",
  )
  return any(h == s or h.endswith("." + s) for s in allowed_suffixes)


@router.get("/proxy-image")
async def proxy_image(
  url: str = Query(..., min_length=12, max_length=2048, description="Абсолютный https URL изображения"),
) -> Response:
  parsed = urlparse(url)
  if parsed.scheme != "https":
    raise HTTPException(status_code=400, detail="Только https://")
  host = (parsed.hostname or "").strip()
  if not _host_allowed_for_proxy(host):
    raise HTTPException(status_code=403, detail="Хост не разрешён для прокси")
  try:
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0), follow_redirects=False) as client:
      target = url
      for attempt in range(5):
        parsed = urlparse(target)
        if parsed.scheme != "https" or parsed.port not in (None, 443) or parsed.username or not _host_allowed_for_proxy(parsed.hostname or ""):
          raise HTTPException(status_code=403, detail="Адрес изображения не разрешён")
        async with client.stream("GET", target, headers=_upstream_headers(target)) as r:
          if r.status_code in (301, 302, 303, 307, 308):
            target = urljoin(target, r.headers.get("location", ""))
            continue
          if r.status_code != 200:
            raise HTTPException(status_code=502, detail="Магазин не отдал фотографию")
          ct = (r.headers.get("content-type") or "").split(";")[0].lower()
          if not (ct.startswith("image/") or ct == "application/octet-stream"):
            raise HTTPException(status_code=502, detail="Магазин вернул не фотографию")
          chunks = bytearray()
          async for chunk in r.aiter_bytes():
            chunks.extend(chunk)
            if len(chunks) > _MAX_BYTES:
              raise HTTPException(status_code=502, detail="Фотография слишком большая")
          body = bytes(chunks)
          if not body:
            raise HTTPException(status_code=502, detail="Пустая фотография")
          break
      else:
        raise HTTPException(status_code=502, detail="Слишком много перенаправлений")
  except httpx.RequestError as e:
    raise HTTPException(status_code=502, detail="Не удалось загрузить фотографию магазина") from e

  return Response(
    content=body,
    media_type=ct,
    headers={"Cache-Control": "public, max-age=300"},
  )
