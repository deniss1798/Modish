"""Bounded per-worker admission without sockets or a database."""
import json
import unittest
from unittest.mock import patch

from starlette.requests import Request
from starlette.responses import Response

from app.middleware import RateLimitMiddleware


class _Clock:
  def __init__(self):
    self.now = 100.0

  def __call__(self):
    return self.now


async def _app(scope, receive, send):
  raise AssertionError("Tests call dispatch directly; no ASGI transport")


class RateLimitLifecycleTests(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.clock = _Clock()
    self.limiter = RateLimitMiddleware(_app, limit=2, max_clients=3, clock=self.clock)
    self.called = []

  async def request(self, ip="192.0.2.1", path="/feed", *, headers=(), limiter=None):
    scope = {"type": "http", "method": "GET", "path": path,
             "headers": list(headers), "client": (ip, 1234) if ip else None,
             "server": ("offline.test", 80), "scheme": "http"}

    async def accepted(request):
      self.called.append((request.client.host if request.client else "anon", path))
      return Response(status_code=200)

    return await (limiter or self.limiter).dispatch(Request(scope), accepted)

  async def test_rolling_window_and_original_429_response_are_preserved(self):
    self.assertEqual((await self.request()).status_code, 200)
    self.clock.now = 130
    self.assertEqual((await self.request()).status_code, 200)
    self.clock.now = 159
    rejected = await self.request()
    self.assertEqual(rejected.status_code, 429)
    self.assertEqual(json.loads(rejected.body), {"detail": "Too many requests"})
    self.assertEqual(len(self.called), 2)
    self.clock.now = 160  # First accepted timestamp expires exactly at 60 sec.
    self.assertEqual((await self.request()).status_code, 200)
    self.assertEqual((await self.request()).status_code, 429)

  async def test_many_one_time_clients_expire_without_their_returning(self):
    limiter = RateLimitMiddleware(_app, limit=2, max_clients=1000, clock=self.clock)
    for index in range(1000):
      response = await self.request(f"2001:db8::{index:x}", limiter=limiter)
      self.assertEqual(response.status_code, 200)
    self.assertEqual(len(limiter._clients), 1000)
    self.clock.now += 60
    self.assertEqual((await self.request("192.0.2.4", limiter=limiter)).status_code, 200)
    self.assertEqual(set(limiter._clients), {"192.0.2.4"})

  async def test_capacity_rejects_new_addresses_without_resetting_active_quotas(self):
    for index in range(3):
      self.assertEqual((await self.request(f"192.0.2.{index}")).status_code, 200)
      self.assertEqual((await self.request(f"192.0.2.{index}")).status_code, 200)
    for index in range(1000):
      self.assertEqual((await self.request(f"2001:db8::{index:x}")).status_code, 429)
    self.assertEqual(len(self.limiter._clients), 3)
    self.assertEqual((await self.request("192.0.2.0")).status_code, 429)
    self.assertEqual(len(self.called), 6)
    self.assertTrue(all(len(bucket.accepted) <= 2 for bucket in self.limiter._clients.values()))

  async def test_capacity_does_not_reject_existing_clients_with_remaining_quota(self):
    for index in range(3):
      await self.request(f"192.0.2.{index}")
    self.assertEqual((await self.request("192.0.2.10")).status_code, 429)
    self.assertEqual((await self.request("192.0.2.0")).status_code, 200)
    self.clock.now += 60
    self.assertEqual((await self.request("192.0.2.10")).status_code, 200)
    self.assertEqual(set(self.limiter._clients), {"192.0.2.10"})

  async def test_rejected_attempt_keeps_active_bucket_and_does_not_count_as_acceptance(self):
    for index in range(3):
      await self.request(f"192.0.2.{index}")
      await self.request(f"192.0.2.{index}")
    self.clock.now += 59
    self.assertEqual((await self.request("192.0.2.0")).status_code, 429)
    self.clock.now += 1
    self.assertEqual((await self.request("192.0.2.4")).status_code, 200)
    self.assertEqual(set(self.limiter._clients), {"192.0.2.0", "192.0.2.4"})
    self.assertEqual((await self.request("192.0.2.0")).status_code, 200)
    self.assertEqual(len(self.limiter._clients["192.0.2.0"].accepted), 1)

  async def test_exempt_endpoints_bypass_capacity_and_do_not_reserve_buckets(self):
    limiter = RateLimitMiddleware(_app, limit=1, max_clients=1, clock=self.clock)
    await self.request("192.0.2.1", limiter=limiter)
    for path in ("/health", "/docs", "/openapi.json", "/redoc"):
      self.assertEqual((await self.request("192.0.2.2", path, limiter=limiter)).status_code, 200)
    self.assertEqual(set(limiter._clients), {"192.0.2.1"})
    self.assertEqual((await self.request("192.0.2.2", limiter=limiter)).status_code, 429)

  async def test_forwarded_headers_do_not_change_the_existing_trusted_client_policy(self):
    limiter = RateLimitMiddleware(_app, limit=1, max_clients=3, clock=self.clock)
    self.assertEqual((await self.request(headers=[(b"x-forwarded-for", b"192.0.2.2")], limiter=limiter)).status_code, 200)
    self.assertEqual((await self.request(headers=[(b"x-forwarded-for", b"192.0.2.3")], limiter=limiter)).status_code, 429)
    self.assertEqual((await self.request("192.0.2.4", headers=[(b"x-forwarded-for", b"192.0.2.2")], limiter=limiter)).status_code, 200)

  async def test_missing_client_uses_one_anonymous_bucket(self):
    self.assertEqual((await self.request(None)).status_code, 200)
    self.assertEqual((await self.request(None)).status_code, 200)
    self.assertEqual((await self.request(None)).status_code, 429)
    self.assertEqual(set(self.limiter._clients), {"anon"})

  async def test_app_instances_do_not_share_quotas(self):
    first = RateLimitMiddleware(_app, limit=1, max_clients=1, clock=self.clock)
    second = RateLimitMiddleware(_app, limit=1, max_clients=1, clock=self.clock)
    self.assertEqual((await self.request(limiter=first)).status_code, 200)
    self.assertEqual((await self.request(limiter=first)).status_code, 429)
    self.assertEqual((await self.request(limiter=second)).status_code, 200)

  async def test_default_clock_does_not_read_adjustable_wall_time(self):
    limiter = RateLimitMiddleware(_app, limit=1, max_clients=1)
    with patch("app.middleware.time.time", side_effect=AssertionError("Wall clock is not a duration")):
      self.assertEqual((await self.request(limiter=limiter)).status_code, 200)
      self.assertEqual((await self.request(limiter=limiter)).status_code, 429)

  def test_invalid_configuration_fails_at_startup(self):
    for bounds in ({"limit": 0}, {"max_clients": 0}, {"window": 0}, {"window": -1}):
      with self.subTest(bounds=bounds), self.assertRaises(ValueError):
        RateLimitMiddleware(_app, **bounds)


if __name__ == "__main__":
  unittest.main()
