from __future__ import annotations

import logging
import math
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


def _auth_limit_from_env(name: str, default: int, *, maximum: int) -> int:
  """Reject disabled/unbounded auth quotas without echoing environment values."""
  try:
    value = int(os.getenv(name, str(default)).strip())
  except ValueError:
    raise ValueError(f"{name} must be a positive integer up to {maximum}") from None
  if not 1 <= value <= maximum:
    raise ValueError(f"{name} must be a positive integer up to {maximum}")
  return value


_AUTH_LOGIN_LIMIT = _auth_limit_from_env("AUTH_LOGIN_PER_MINUTE", 10, maximum=60)
_AUTH_REGISTER_LIMIT = _auth_limit_from_env("AUTH_REGISTER_PER_MINUTE", 5, maximum=20)


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
               window: float = _RATE_WINDOW, clock=None,
               auth_login_limit: int = _AUTH_LOGIN_LIMIT,
               auth_register_limit: int = _AUTH_REGISTER_LIMIT,
               auth_window: float = _RATE_WINDOW):
    super().__init__(app)
    if limit < 1 or max_clients < 1 or window <= 0:
      raise ValueError("Rate-limit bounds must be positive")
    if (type(auth_login_limit) is not int or not 1 <= auth_login_limit <= 60
        or type(auth_register_limit) is not int or not 1 <= auth_register_limit <= 20
        or not isinstance(auth_window, (int, float)) or isinstance(auth_window, bool)
        or not math.isfinite(auth_window) or auth_window <= 0):
      raise ValueError("Auth rate-limit bounds must be positive, finite and bounded")
    self.limit = limit
    self.max_clients = max_clients
    self.window = window
    self._clock = clock or time.monotonic
    self._clients: OrderedDict[str, _ClientWindow] = OrderedDict()
    self.auth_window = auth_window
    self._auth_limits = {"/auth/login": auth_login_limit, "/auth/register": auth_register_limit}
    # Two fixed paths, each bounded by max_clients. Auth traffic does not spend
    # the independent general quota, and general traffic cannot reset auth.
    self._auth_clients: dict[str, OrderedDict[str, _ClientWindow]] = {
      path: OrderedDict() for path in self._auth_limits
    }

  def _expire_idle(self, now: float, *, clients=None, window=None) -> None:
    # Entries follow their last attempt, so expiration only visits the oldest
    # idle clients. Active/rejected clients retain their quota at capacity.
    clients = self._clients if clients is None else clients
    window = self.window if window is None else window
    while clients:
      ip, bucket = next(iter(clients.items()))
      if now - bucket.touched_at < window:
        break
      clients.pop(ip)

  @staticmethod
  def _rejected() -> JSONResponse:
    return JSONResponse(status_code=429, content={"detail": "Too many requests"})

  def _auth_rejected(self, path: str, retry_after: float) -> JSONResponse:
    seconds = max(1, math.ceil(retry_after))
    action = "входа" if path == "/auth/login" else "регистрации"
    return JSONResponse(status_code=429,
      content={"detail": f"Слишком много попыток {action}. Подождите {seconds} сек. и попробуйте снова."},
      headers={"Retry-After": str(seconds), "Cache-Control": "no-store"})

  def _admit_auth(self, ip: str, path: str, now: float) -> JSONResponse | None:
    # Prune both fixed path stores on auth traffic, including one-time clients
    # that never return. Capacity never evicts an active client's spent quota.
    for clients in self._auth_clients.values():
      self._expire_idle(now, clients=clients, window=self.auth_window)
    clients = self._auth_clients[path]
    bucket = clients.get(ip)
    if bucket is None:
      if len(clients) >= self.max_clients:
        oldest = next(iter(clients.values()))
        return self._auth_rejected(path, oldest.touched_at + self.auth_window - now)
      bucket = _ClientWindow(touched_at=now)
      clients[ip] = bucket
    else:
      bucket.touched_at = now
      clients.move_to_end(ip)
    while bucket.accepted and now - bucket.accepted[0] >= self.auth_window:
      bucket.accepted.popleft()
    if len(bucket.accepted) >= self._auth_limits[path]:
      return self._auth_rejected(path, bucket.accepted[0] + self.auth_window - now)
    # Admission is synchronous until call_next: concurrent tasks cannot pass
    # the same last slot, and failed credentials/invalid payloads still count.
    bucket.accepted.append(now)
    return None

  async def dispatch(self, request: Request, call_next: Callable) -> Response:
    if request.url.path in (
      "/health",
      "/health/live",
      "/health/ready",
      "/docs",
      "/openapi.json",
      "/redoc",
    ):
      return await call_next(request)

    ip = request.client.host if request.client else "anon"
    if request.url.path in self._auth_limits:
      # Only POST can run password hashing/account creation. In particular,
      # browser preflight OPTIONS must not spend a real sign-in attempt.
      if request.method != "POST":
        return await call_next(request)
      rejection = self._admit_auth(ip, request.url.path, self._clock())
      if rejection is not None:
        return rejection
      return await call_next(request)
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
