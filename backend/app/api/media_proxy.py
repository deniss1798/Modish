"""Прокси изображений для клиентов без исходящего HTTPS (типично Android-эмулятор)."""
from __future__ import annotations

import asyncio
import ipaddress
from urllib.parse import urlparse, urljoin

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from ..services.public_fetch import (
  BoundedBody, BodyRejected, FetchDenied, MAX_REDIRECTS, REDIRECT_STATUSES, STREAM_CHUNK_BYTES,
  resolve_public_target_async,
)

router = APIRouter(prefix="/media", tags=["media"])

_MAX_BYTES = 12 * 1024 * 1024
_TOTAL_TIMEOUT_SECONDS = 30


def _upstream_headers(target: str) -> dict[str, str]:
  ua = (
    "Mozilla/5.0 (Linux; Android 13; Mobile) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
  )
  h: dict[str, str] = {
    "User-Agent": ua,
    "Accept": "image/jpeg,image/png,image/webp,image/apng,image/*,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "identity",
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
  # An origin allowlist is additional to DNS/IP admission, never its substitute.
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


async def _download_image(url: str) -> tuple[bytes, str]:
    target = url
    for hop in range(MAX_REDIRECTS + 1):
      parsed = urlparse(target)
      if parsed.scheme != "https" or not _host_allowed_for_proxy(parsed.hostname or ""):
        raise FetchDenied('nonpublic_image_origin')
      public = await resolve_public_target_async(target)
      headers = _upstream_headers(public.url)
      headers['Host'] = public.host_header
      # No shared-IP TLS origin reuse, ambient proxy or cross-origin cookies.
      async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0),
                                   follow_redirects=False, trust_env=False) as client:
        async with client.stream("GET", public.connect_url, headers=headers,
                                 extensions=public.extensions) as r:
          if r.status_code in REDIRECT_STATUSES:
            location = r.headers.get('location')
            if not location or hop == MAX_REDIRECTS:
              raise FetchDenied('invalid_image_redirect')
            target = urljoin(public.url, location)
            continue
          if r.status_code != 200:
            raise HTTPException(status_code=502, detail="Магазин не отдал фотографию")
          ct = (r.headers.get("content-type") or "").split(";")[0].lower()
          if ct not in {'image/jpeg', 'image/png', 'image/webp', 'image/apng',
                        'image/gif', 'image/avif', 'application/octet-stream'}:
            raise HTTPException(status_code=502, detail="Магазин вернул не фотографию")
          bounded = BoundedBody(r.headers, _MAX_BYTES)
          async for chunk in r.aiter_raw(chunk_size=STREAM_CHUNK_BYTES):
            bounded.add(chunk)
          body = bounded.finish()
          break
    else:
      raise FetchDenied('invalid_image_redirect')
    return body, ct


@router.get("/proxy-image")
async def proxy_image(
  url: str = Query(..., min_length=12, max_length=2048, description="Абсолютный https URL изображения"),
) -> Response:
  try:
    async with asyncio.timeout(_TOTAL_TIMEOUT_SECONDS):
      body, ct = await _download_image(url)
  except BodyRejected:
    raise HTTPException(status_code=502, detail="Фото магазина недоступно. Выберите другую вещь или повторите позже.") from None
  except (FetchDenied, ValueError):
    raise HTTPException(status_code=403, detail="Не удалось загрузить фото. Выберите другую вещь или повторите позже.") from None
  except (httpx.RequestError, TimeoutError):
    raise HTTPException(status_code=502, detail="Не удалось загрузить фото магазина. Повторите попытку позже.") from None

  return Response(
    content=body,
    media_type=ct,
    headers={"Cache-Control": "public, max-age=300",
             "X-Content-Type-Options": "nosniff",
             "Content-Security-Policy": "default-src 'none'; sandbox"},
  )
