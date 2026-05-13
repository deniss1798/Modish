"""Прокси изображений для клиентов без исходящего HTTPS (типично Android-эмулятор)."""
from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

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
    "picsum.photos",
    "fastly.picsum.photos",
    "placehold.co",
    "dummyimage.com",
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
  headers = _upstream_headers(url)
  try:
    async with httpx.AsyncClient(
      timeout=httpx.Timeout(35.0, connect=25.0),
      follow_redirects=True,
      limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
    ) as client:
      r = await client.get(url, headers=headers)
  except httpx.RequestError as e:
    raise HTTPException(status_code=502, detail=f"Upstream error: {e!s}") from e

  if r.status_code != 200:
    raise HTTPException(status_code=502, detail=f"Upstream HTTP {r.status_code}")

  body = r.content
  if len(body) > _MAX_BYTES:
    raise HTTPException(status_code=502, detail="Слишком большой ответ")

  ct = (r.headers.get("content-type") or "image/jpeg").split(";")[0].strip().lower()
  if "text/html" in ct or "application/json" in ct:
    raise HTTPException(status_code=502, detail="Upstream вернул не изображение")
  if ct and not ct.startswith("image/") and "octet-stream" not in ct:
    # Некоторые CDN отдают application/octet-stream
    ct = "image/jpeg"

  return Response(
    content=body,
    media_type=ct,
    headers={"Cache-Control": "public, max-age=300"},
  )
