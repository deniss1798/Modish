from __future__ import annotations

import logging
import os
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger("modish.api")

# Per-worker limits; a distributed deployment needs a shared limiter.
_RATE_LIMIT = int(os.getenv("RATE_LIMIT_PER_MINUTE", "120"))
_RATE_WINDOW = 60.0
_RATE_MAX_CLIENTS = int(os.getenv("RATE_LIMIT_MAX_CLIENTS", "4096"))


@dataclass(slots=True)
class _ClientWindow:
  touched_at: float
  accepted: deque[float] = field(default_factory=deque)


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
  def __init__(self, app, *, limit: int = _RATE_LIMIT,
               max_clients: int = _RATE_MAX_CLIENTS,
               window: float = _RATE_WINDOW, clock=None):
    super().__init__(app)
    if limit < 1 or max_clients < 1 or window <= 0:
      raise ValueError("Rate-limit bounds must be positive")
    self.limit = limit
    self.max_clients = max_clients
    self.window = window
    self._clock = clock or time.monotonic
    self._clients: OrderedDict[str, _ClientWindow] = OrderedDict()

  def _expire_idle(self, now: float) -> None:
    # Entries follow their last attempt, so expiration only visits the oldest
    # idle clients. Active/rejected clients retain their quota at capacity.
    while self._clients:
      ip, bucket = next(iter(self._clients.items()))
      if now - bucket.touched_at < self.window:
        break
      self._clients.pop(ip)

  @staticmethod
  def _rejected() -> JSONResponse:
    return JSONResponse(status_code=429, content={"detail": "Too many requests"})

  async def dispatch(self, request: Request, call_next: Callable) -> Response:
    if request.url.path in (
      "/health",
      "/docs",
      "/openapi.json",
      "/redoc",
      "/auth/login",
      "/auth/register",
    ):
      return await call_next(request)

    ip = request.client.host if request.client else "anon"
    now = self._clock()
    self._expire_idle(now)
    bucket = self._clients.get(ip)
    if bucket is None:
      # Never evict an active client to admit a rotating, untracked address.
      # This explicit backpressure bounds memory without resetting quotas.
      if len(self._clients) >= self.max_clients:
        return self._rejected()
      bucket = _ClientWindow(touched_at=now)
      self._clients[ip] = bucket
    else:
      bucket.touched_at = now
      self._clients.move_to_end(ip)
    while bucket.accepted and now - bucket.accepted[0] >= self.window:
      bucket.accepted.popleft()
    if len(bucket.accepted) >= self.limit:
      return self._rejected()
    bucket.accepted.append(now)
    return await call_next(request)
