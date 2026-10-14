"""Auth admission runs before credentials/DB work; no sockets or live services."""
import asyncio
import json
import os
import traceback
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import Response

from app.middleware import RateLimitMiddleware, _auth_limit_from_env


class _Clock:
  def __init__(self):
    self.now = 100.0

  def __call__(self):
    return self.now


async def _unused(scope, receive, send):
  raise AssertionError("Direct middleware tests must not open an ASGI transport")


class AuthRateLimitTests(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.clock = _Clock()
    self.limiter = RateLimitMiddleware(_unused, limit=2, max_clients=3,
      clock=self.clock, auth_login_limit=2, auth_register_limit=1)
    self.accepted = []

  async def request(self, path="/auth/login", *, ip="192.0.2.1", headers=(),
                    method="POST", query=b"", limiter=None, downstream=None):
    scope = {"type": "http", "method": method, "path": path, "query_string": query,
      "headers": list(headers), "client": (ip, 1234) if ip else None,
      "server": ("offline.test", 80), "scheme": "http"}

    async def accepted(request):
      self.accepted.append((request.client.host if request.client else "anon", path))
      return await downstream(request) if downstream else Response(status_code=200)

    return await (limiter or self.limiter).dispatch(Request(scope), accepted)

  async def test_login_and_registration_have_independent_per_ip_path_quotas(self):
    self.assertEqual((await self.request()).status_code, 200)
    self.assertEqual((await self.request()).status_code, 200)
    self.assertEqual((await self.request()).status_code, 429)
    self.assertEqual((await self.request("/auth/register")).status_code, 200)
    self.assertEqual((await self.request("/auth/register")).status_code, 429)
    self.assertEqual((await self.request(ip="192.0.2.2")).status_code, 200)
    self.assertEqual(self.limiter._clients, {})

  async def test_rolling_window_returns_precise_retry_after_and_next_step(self):
    await self.request()
    self.clock.now = 130
    await self.request()
    self.clock.now = 158.2
    rejected = await self.request()
    self.assertEqual(rejected.status_code, 429)
    self.assertEqual(rejected.headers["Retry-After"], "2")
    self.assertEqual(rejected.headers["Cache-Control"], "no-store")
    self.assertEqual(json.loads(rejected.body), {"detail":
      "Слишком много попыток входа. Подождите 2 сек. и попробуйте снова."})
    self.clock.now = 160
    self.assertEqual((await self.request()).status_code, 200)
    self.assertEqual((await self.request()).headers["Retry-After"], "30")

  async def test_forwarded_headers_query_and_host_cannot_rotate_an_auth_identity(self):
    for address in (b"192.0.2.11", b"192.0.2.12"):
      response = await self.request(headers=[(b"x-forwarded-for", address),
        (b"x-real-ip", address), (b"forwarded", b"for=" + address), (b"host", address)],
        query=b"email=" + address)
      self.assertEqual(response.status_code, 200)
    self.assertEqual((await self.request(headers=[(b"x-forwarded-for", b"192.0.2.13")])).status_code, 429)
    self.assertEqual(list(self.limiter._auth_clients["/auth/login"]), ["192.0.2.1"])
    self.assertEqual((await self.request(ip="192.0.2.2")).status_code, 200)

  async def test_general_traffic_and_auth_counters_do_not_clear_each_other(self):
    for _ in range(2):
      self.assertEqual((await self.request("/feed", method="GET")).status_code, 200)
      self.assertEqual((await self.request()).status_code, 200)
    self.assertEqual((await self.request("/feed", method="GET")).status_code, 429)
    self.assertEqual((await self.request()).status_code, 429)
    self.assertEqual((await self.request("/auth/register")).status_code, 200)

  async def test_auth_capacity_never_evicts_active_quotas(self):
    for index in range(3):
      for _ in range(2):
        self.assertEqual((await self.request(ip=f"192.0.2.{index}")).status_code, 200)
    for index in range(300):
      response = await self.request(ip=f"2001:db8::{index:x}")
      self.assertEqual(response.status_code, 429)
      self.assertEqual(response.headers["Retry-After"], "60")
    self.assertEqual(len(self.limiter._auth_clients["/auth/login"]), 3)
    self.assertEqual((await self.request(ip="192.0.2.0")).status_code, 429)
    self.assertTrue(all(len(bucket.accepted) <= 2
      for bucket in self.limiter._auth_clients["/auth/login"].values()))

  async def test_auth_idle_ttl_prunes_one_time_clients_across_both_paths(self):
    for index in range(3):
      await self.request(ip=f"192.0.2.{index}")
      await self.request("/auth/register", ip=f"192.0.2.{index}")
    self.clock.now += 60
    self.assertEqual((await self.request(ip="192.0.2.4")).status_code, 200)
    self.assertEqual(set(self.limiter._auth_clients["/auth/login"]), {"192.0.2.4"})
    self.assertEqual(self.limiter._auth_clients["/auth/register"], {})

  async def test_rejected_attempt_retains_client_without_spending_a_new_slot(self):
    await self.request("/auth/register")
    self.clock.now += 59
    self.assertEqual((await self.request("/auth/register")).status_code, 429)
    self.clock.now += 1
    self.assertEqual((await self.request("/auth/register")).status_code, 200)
    bucket = self.limiter._auth_clients["/auth/register"]["192.0.2.1"]
    self.assertEqual(list(bucket.accepted), [160])

  async def test_cors_preflight_and_non_post_do_not_consume_auth_attempts(self):
    for _ in range(5):
      for method in ("OPTIONS", "GET", "HEAD"):
        self.assertEqual((await self.request("/auth/register", method=method)).status_code, 200)
    self.assertEqual(self.limiter._auth_clients["/auth/register"], {})
    self.assertEqual((await self.request("/auth/register")).status_code, 200)
    self.assertEqual((await self.request("/auth/register")).status_code, 429)

  async def test_anonymous_clients_share_one_auth_bucket(self):
    await self.request("/auth/register", ip=None)
    self.assertEqual((await self.request("/auth/register", ip=None)).status_code, 429)
    self.assertEqual(list(self.limiter._auth_clients["/auth/register"]), ["anon"])

  async def test_concurrent_last_slot_runs_credentials_work_once(self):
    limiter = RateLimitMiddleware(_unused, clock=self.clock, auth_login_limit=1)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def credentials(request):
      entered.set()
      await release.wait()
      return Response(status_code=401)

    first = asyncio.create_task(self.request(limiter=limiter, downstream=credentials))
    await asyncio.wait_for(entered.wait(), 1)
    responses = await asyncio.gather(*(self.request(limiter=limiter) for _ in range(10)))
    self.assertEqual([response.status_code for response in responses], [429] * 10)
    release.set()
    self.assertEqual((await first).status_code, 401)
    self.assertEqual(len(self.accepted), 1)

  async def test_http_admission_counts_failed_credentials_and_registration_conflicts(self):
    app = FastAPI()
    calls = {"login": 0, "register": 0}

    @app.post("/auth/login")
    async def login():
      calls["login"] += 1
      return Response(status_code=401)

    @app.post("/auth/register")
    async def register():
      calls["register"] += 1
      return Response(status_code=409)

    app.add_middleware(RateLimitMiddleware, clock=self.clock,
      auth_login_limit=2, auth_register_limit=1)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
        base_url="http://offline.test") as client:
      self.assertEqual((await client.post("/auth/login")).status_code, 401)
      self.assertEqual((await client.post("/auth/login")).status_code, 401)
      rejected = await client.post("/auth/login", headers={"X-Forwarded-For": "192.0.2.100"})
      self.assertEqual(rejected.status_code, 429)
      self.assertEqual(rejected.headers["Retry-After"], "60")
      self.assertIn("попробуйте снова", rejected.json()["detail"])
      self.assertEqual((await client.post("/auth/register")).status_code, 409)
      self.assertEqual((await client.post("/auth/register")).status_code, 429)
    self.assertEqual(calls, {"login": 2, "register": 1})

  async def test_safe_defaults_are_enforced_without_configuration(self):
    limiter = RateLimitMiddleware(_unused, clock=self.clock)
    for path, limit in (("/auth/login", 10), ("/auth/register", 5)):
      for _ in range(limit):
        self.assertEqual((await self.request(path, limiter=limiter)).status_code, 200)
      self.assertEqual((await self.request(path, limiter=limiter)).status_code, 429)

  def test_configuration_does_not_allow_disabled_or_unbounded_auth_limits(self):
    for bounds in ({"auth_login_limit": 0}, {"auth_login_limit": 61},
        {"auth_register_limit": 0}, {"auth_register_limit": 21},
        {"auth_login_limit": True}, {"auth_window": 0},
        {"auth_window": float("inf")}, {"auth_window": float("nan")}):
      with self.subTest(bounds=bounds), self.assertRaises(ValueError):
        RateLimitMiddleware(_unused, **bounds)
    for raw in ("0", "-1", "999999", "not-an-integer"):
      with patch.dict(os.environ, {"AUTH_LOGIN_PER_MINUTE": raw}):
        with self.assertRaisesRegex(ValueError, "AUTH_LOGIN_PER_MINUTE must be"):
          _auth_limit_from_env("AUTH_LOGIN_PER_MINUTE", 10, maximum=60)
    with patch.dict(os.environ, {"AUTH_LOGIN_PER_MINUTE": "7"}):
      self.assertEqual(_auth_limit_from_env("AUTH_LOGIN_PER_MINUTE", 10, maximum=60), 7)

  def test_invalid_environment_value_is_not_echoed_in_startup_traceback(self):
    marker = "unexpected-config-value-for-redaction"
    with patch.dict(os.environ, {"AUTH_LOGIN_PER_MINUTE": marker}):
      try:
        _auth_limit_from_env("AUTH_LOGIN_PER_MINUTE", 10, maximum=60)
      except ValueError as error:
        diagnostic = "".join(traceback.format_exception(error))
      else:
        self.fail("Invalid auth configuration must fail startup")
    self.assertIn("AUTH_LOGIN_PER_MINUTE", diagnostic)
    self.assertNotIn(marker, diagnostic)
