from __future__ import annotations

import logging
import os
import time
from collections import defaultdict
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger("modish.api")

# Простой in-memory rate limit (P8); для кластера — Redis.
_RATE: dict[str, list[float]] = defaultdict(list)
_RATE_LIMIT = int(os.getenv("RATE_LIMIT_PER_MINUTE", "120"))
_RATE_WINDOW = 60.0


class RequestLogMiddleware(BaseHTTPMiddleware):
  async def dispatch(self, request: Request, call_next: Callable) -> Response:
    start = time.perf_counter()
    response = await call_next(request)
    ms = (time.perf_counter() - start) * 1000
    logger.info(
      "%s %s %s %.0fms",
      request.method,
      request.url.path,
      response.status_code,
      ms,
    )
    return response


class RateLimitMiddleware(BaseHTTPMiddleware):
  async def dispatch(self, request: Request, call_next: Callable) -> Response:
    if request.url.path in ("/health", "/docs", "/openapi.json", "/redoc"):
      return await call_next(request)

    ip = request.client.host if request.client else "anon"
    now = time.time()
    bucket = _RATE[ip]
    bucket[:] = [t for t in bucket if now - t < _RATE_WINDOW]
    if len(bucket) >= _RATE_LIMIT:
      return JSONResponse(
        status_code=429,
        content={"detail": "Too many requests"},
      )
    bucket.append(now)
    return await call_next(request)
