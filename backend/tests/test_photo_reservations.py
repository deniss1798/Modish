"""Lifecycle tests on SQLite; PostgreSQL lock admission is a separate gate."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app import business
from app.api.deps import create_access_token, get_db
from app.db import Base
from app.main import app
from app.models import PhotoAnalysisReservation, StyleProfile, User, UserLimits
from app.services import photo_analysis_jobs as jobs


ANALYSIS = {
  "color_palette": ["navy"], "avoid_colors": [], "contrast_level": "low",
  "body_proportions": "balanced", "recommended_silhouettes": ["straight"],
  "avoid_silhouettes": [], "recommended_items": ["shirt"], "avoid_items": [],
  "style_directions": ["casual"], "summary": "Test analysis",
}
PENDING = {"photo_analysis": ANALYSIS, "body_shape": "Rectangle",
           "color_type": "Neutral", "height_category": None, "summary": "Test analysis"}
ENDPOINTS = (
  ("/onboarding/photo", "app.api.onboarding.analyze_onboarding_photo", PENDING),
  ("/style-profile/analyze", "app.api.profile.analyze_photo_ai", ANALYSIS),
)


class PhotoReservationTests(unittest.IsolatedAsyncioTestCase):
  def setUp(self):
    self.directory = tempfile.TemporaryDirectory()
    self.engine = create_engine(
      "sqlite:///" + str(Path(self.directory.name) / "photo.db"),
      connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(self.engine)
    self.sessions = sessionmaker(bind=self.engine, autoflush=False)
    self.session_patch = patch.object(jobs, "SessionLocal", self.sessions)
    self.session_patch.start()
    self.old_overrides = dict(app.dependency_overrides)
    def get_test_db():
      with self.sessions() as db:
        yield db
    app.dependency_overrides[get_db] = get_test_db
    self.make_user()

  def tearDown(self):
    app.dependency_overrides.clear()
    app.dependency_overrides.update(self.old_overrides)
    self.session_patch.stop()
    self.engine.dispose()
    self.directory.cleanup()

  def make_user(self):
    self.user_id = str(uuid4())
    with self.sessions() as db:
      db.add(User(id=self.user_id, email=f"{uuid4().hex}@example.com",
                  password_hash="unused", subscription_status="active"))
      db.flush()
      db.add(StyleProfile(id=str(uuid4()), user_id=self.user_id,
                          profile_json={"analysis": deepcopy(ANALYSIS)}))
      db.commit()
    self.token = create_access_token(self.user_id)
    self.credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=self.token)

  def client(self):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

  async def post(self, client, path, key="photo-1", raw=b"fake-photo"):
    headers = {"Authorization": f"Bearer {self.token}"}
    if key is not None:
      headers["Idempotency-Key"] = key
    return await client.post(path, headers=headers, files={"photo": ("photo.jpg", raw, "image/jpeg")})

  def usage(self):
    with self.sessions() as db:
      row = db.scalar(select(UserLimits).where(UserLimits.user_id == self.user_id))
      return row.photo_analysis_used if row else 0

  async def test_both_endpoints_replay_and_check_in_connection_before_provider(self):
    for path, target, result in ENDPOINTS:
      with self.subTest(path=path):
        self.make_user()
        calls = []
        async def provider(**kwargs):
          calls.append(1)
          self.assertEqual(self.engine.pool.checkedout(), 0)
          with self.sessions() as db:
            self.assertEqual(db.scalar(text("SELECT 1")), 1)
          return deepcopy(result)
        with patch(target, provider):
          async with self.client() as client:
            first = await self.post(client, path)
            replay = await self.post(client, path)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(replay.json(), first.json())
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.usage(), 1)
        self.assertEqual(self.engine.pool.checkedout(), 0)

  async def test_in_flight_duplicate_conflicts_and_other_key_cannot_take_slot(self):
    for path, target, result in ENDPOINTS:
      with self.subTest(path=path):
        self.make_user()
        entered, finish = asyncio.Event(), asyncio.Event()
        calls = []
        async def provider(**kwargs):
          calls.append(1)
          entered.set()
          await finish.wait()
          return deepcopy(result)
        with patch(target, provider):
          async with self.client() as client:
            first = asyncio.create_task(self.post(client, path))
            try:
              await asyncio.wait_for(entered.wait(), 3)
              self.assertEqual(self.engine.pool.checkedout(), 0)
              self.assertEqual((await client.get("/health")).json()["database"], "ok")
              self.assertEqual((await self.post(client, path)).status_code, 409)
              self.assertEqual((await self.post(client, path, "different")).status_code, 403)
            finally:
              finish.set()
            self.assertEqual((await first).status_code, 200)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.usage(), 1)

  async def test_provider_failure_refunds_once_and_failed_key_is_terminal(self):
    for path, target, _ in ENDPOINTS:
      with self.subTest(path=path):
        self.make_user()
        async def provider(**kwargs):
          raise RuntimeError("private provider diagnostic")
        with patch(target, provider):
          async with self.client() as client:
            failed = await self.post(client, path)
            self.assertEqual(failed.status_code, 502)
            self.assertNotIn("private provider", failed.text)
            self.assertEqual((await self.post(client, path)).status_code, 409)
        self.assertEqual(self.usage(), 0)
        with self.sessions() as db:
          row = db.scalar(select(PhotoAnalysisReservation).where(PhotoAnalysisReservation.user_id == self.user_id))
          saved = jobs.ReservedPhoto(row.id, row.user_id)
          self.assertEqual(row.status, "failed")
        jobs.release_photo(saved)
        self.assertEqual(self.usage(), 0)
        self.assertEqual(self.engine.pool.checkedout(), 0)

  async def test_both_endpoint_cancellations_stop_provider_and_release(self):
    for path, target, _ in ENDPOINTS:
      with self.subTest(path=path):
        self.make_user()
        entered, stopped = asyncio.Event(), asyncio.Event()
        async def provider(**kwargs):
          entered.set()
          try:
            await asyncio.Event().wait()
          finally:
            stopped.set()
        with patch(target, provider):
          async with self.client() as client:
            task = asyncio.create_task(self.post(client, path))
            await asyncio.wait_for(entered.wait(), 3)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
              await task
        self.assertTrue(stopped.is_set())
        self.assertEqual(self.usage(), 0)
        with self.sessions() as db:
          self.assertEqual(db.scalar(select(PhotoAnalysisReservation.status).where(
            PhotoAnalysisReservation.user_id == self.user_id)), "cancelled")
        self.assertEqual(self.engine.pool.checkedout(), 0)

  async def test_timeout_waits_for_provider_cancellation_before_refund(self):
    stopped = asyncio.Event()
    async def provider(**kwargs):
      try:
        await asyncio.Event().wait()
      finally:
        stopped.set()
    with patch(ENDPOINTS[0][1], provider), patch.object(jobs, "PHOTO_PROVIDER_TIMEOUT_SECONDS", 0.01):
      async with self.client() as client:
        result = await self.post(client, ENDPOINTS[0][0])
    self.assertEqual(result.status_code, 504)
    self.assertTrue(stopped.is_set())
    self.assertEqual(self.usage(), 0)

  async def test_payload_and_cross_endpoint_key_conflicts_do_not_call_provider(self):
    calls = []
    async def provider(**kwargs):
      calls.append(1)
      return deepcopy(PENDING)
    with patch(ENDPOINTS[0][1], provider), patch(ENDPOINTS[1][1], provider):
      async with self.client() as client:
        self.assertEqual((await self.post(client, ENDPOINTS[0][0])).status_code, 200)
        self.assertEqual((await self.post(client, ENDPOINTS[0][0], raw=b"changed")).status_code, 409)
        self.assertEqual((await self.post(client, ENDPOINTS[1][0])).status_code, 409)
    self.assertEqual(len(calls), 1)

  async def test_missing_key_is_backwards_compatible_invalid_key_has_no_debit(self):
    async def provider(**kwargs):
      return deepcopy(PENDING)
    with patch(ENDPOINTS[0][1], provider):
      async with self.client() as client:
        self.assertEqual((await self.post(client, ENDPOINTS[0][0], "bad key")).status_code, 400)
        self.assertEqual(self.usage(), 0)
        self.assertEqual((await self.post(client, ENDPOINTS[0][0], None)).status_code, 200)

  def test_refund_after_month_transition_never_refunds_new_period(self):
    october = datetime(2026, 10, 31, 23, 59, 50, tzinfo=timezone.utc)
    november = october + timedelta(seconds=20)
    with patch.object(jobs, "_now", return_value=october):
      old = jobs.reserve_photo(self.credentials, "october", "style_profile", "a" * 64)
    with patch.object(jobs, "_now", return_value=november):
      new = jobs.reserve_photo(self.credentials, "november", "style_profile", "b" * 64)
      jobs.release_photo(old, "cancelled")
      jobs.release_photo(old, "cancelled")
      self.assertEqual(self.usage(), 1)
      jobs.release_photo(new)
      self.assertEqual(self.usage(), 0)

  def test_expired_unknown_operation_keeps_charge_and_cannot_write_late_result(self):
    now = datetime(2026, 10, 13, tzinfo=timezone.utc)
    with patch.object(jobs, "_now", return_value=now):
      reservation = jobs.reserve_photo(self.credentials, "expired", "style_profile", "a" * 64)
    later = now + timedelta(seconds=jobs.PHOTO_RESERVATION_TTL_SECONDS + 1)
    with patch.object(jobs, "_now", return_value=later):
      with self.assertRaises(HTTPException) as duplicate:
        jobs.reserve_photo(self.credentials, "expired", "style_profile", "a" * 64)
      self.assertEqual(duplicate.exception.status_code, 409)
      with self.assertRaises(HTTPException) as another:
        jobs.reserve_photo(self.credentials, "another", "style_profile", "b" * 64)
      self.assertEqual(another.exception.status_code, 403)
      with self.assertRaises(HTTPException):
        jobs.complete_photo(reservation, {**ANALYSIS, "summary": "late"})
    self.assertEqual(self.usage(), 1)
    with self.sessions() as db:
      self.assertEqual(db.get(PhotoAnalysisReservation, reservation.id).status, "expired")
      profile = db.scalar(select(StyleProfile).where(StyleProfile.user_id == self.user_id))
      self.assertEqual(profile.profile_json["analysis"]["summary"], "Test analysis")

  def test_late_prior_month_result_does_not_overwrite_newer_completed_analysis(self):
    old_time = datetime(2026, 10, 31, 23, 59, 50, tzinfo=timezone.utc)
    new_time = old_time + timedelta(seconds=20)
    with patch.object(jobs, "_now", return_value=old_time):
      old = jobs.reserve_photo(self.credentials, "old", "style_profile", "a" * 64)
    with patch.object(jobs, "_now", return_value=new_time):
      new = jobs.reserve_photo(self.credentials, "new", "style_profile", "b" * 64)
      jobs.complete_photo(new, {**ANALYSIS, "summary": "newer"})
      with self.assertRaises(HTTPException):
        jobs.complete_photo(old, {**ANALYSIS, "summary": "older"})
    self.assertEqual(self.usage(), 1)
    with self.sessions() as db:
      self.assertEqual(db.get(PhotoAnalysisReservation, old.id).status, "superseded")
      profile = db.scalar(select(StyleProfile).where(StyleProfile.user_id == self.user_id))
      self.assertEqual(profile.profile_json["analysis"]["summary"], "newer")

  async def test_visual_provider_receives_detached_copy_without_checked_out_connection(self):
    async def provider(analysis):
      self.assertEqual(self.engine.pool.checkedout(), 0)
      analysis["color_palette"].append("changed-in-provider")
      with self.sessions() as db:
        self.assertEqual(db.scalar(text("SELECT 1")), 1)
      return "https://example.com/generated.jpg"
    with patch("app.main.generate_style_visual", provider):
      async with self.client() as client:
        response = await client.post("/visual-analysis", headers={"Authorization": f"Bearer {self.token}"})
    self.assertEqual(response.status_code, 200)
    with self.sessions() as db:
      profile = db.scalar(select(StyleProfile).where(StyleProfile.user_id == self.user_id))
      self.assertEqual(profile.profile_json["analysis"]["color_palette"], ["navy"])

  async def test_visual_failure_does_not_echo_provider_diagnostic(self):
    async def provider(analysis):
      self.assertEqual(self.engine.pool.checkedout(), 0)
      raise RuntimeError("private-provider-url-and-payload")
    with patch("app.main.generate_style_visual", provider):
      async with self.client() as client:
        response = await client.post("/visual-analysis", headers={"Authorization": f"Bearer {self.token}"})
    self.assertEqual(response.status_code, 502)
    self.assertNotIn("private-provider", response.text)
    self.assertEqual(self.engine.pool.checkedout(), 0)

  async def test_repeated_native_cancel_during_admission_refunds_without_starting_provider(self):
    entered, finish = threading.Event(), threading.Event()
    original = jobs.reserve_photo
    calls = []
    def delayed_reserve(*args):
      reserved = original(*args)
      entered.set()
      if not finish.wait(5):
        raise AssertionError("admission_test_gate_timeout")
      return reserved
    async def provider(**kwargs):
      calls.append(1)
      return deepcopy(ANALYSIS)
    with patch.object(jobs, "reserve_photo", delayed_reserve):
      task = asyncio.create_task(jobs.run_photo_analysis(credentials=self.credentials,
        request_id="cancel-admission", kind="style_profile", content_type="image/jpeg", raw=b"image", provider=provider))
      try:
        self.assertTrue(await asyncio.to_thread(entered.wait, 3))
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
      finally:
        finish.set()
      with self.assertRaises(asyncio.CancelledError):
        await task
    self.assertEqual(calls, [])
    self.assertEqual(self.usage(), 0)
    self.assertEqual(self.engine.pool.checkedout(), 0)

  async def test_repeated_native_cancel_during_refund_waits_for_exact_release(self):
    entered, finish = threading.Event(), threading.Event()
    original = jobs.release_photo
    def delayed_release(*args):
      entered.set()
      if not finish.wait(5):
        raise AssertionError("refund_test_gate_timeout")
      return original(*args)
    async def provider(**kwargs):
      raise RuntimeError("controlled provider failure")
    with patch.object(jobs, "release_photo", delayed_release):
      task = asyncio.create_task(jobs.run_photo_analysis(credentials=self.credentials,
        request_id="cancel-refund", kind="style_profile", content_type="image/jpeg", raw=b"image", provider=provider))
      try:
        self.assertTrue(await asyncio.to_thread(entered.wait, 3))
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
      finally:
        finish.set()
      with self.assertRaises(asyncio.CancelledError):
        await task
    self.assertEqual(self.usage(), 0)
    self.assertEqual(self.engine.pool.checkedout(), 0)

  async def test_repeated_native_cancel_during_completion_keeps_committed_replay(self):
    entered, finish = threading.Event(), threading.Event()
    original = jobs.complete_photo
    calls = []
    def delayed_complete(*args):
      response = original(*args)
      entered.set()
      if not finish.wait(5):
        raise AssertionError("completion_test_gate_timeout")
      return response
    async def provider(**kwargs):
      calls.append(1)
      return deepcopy(ANALYSIS)
    kwargs = dict(credentials=self.credentials, request_id="cancel-completion", kind="style_profile",
                  content_type="image/jpeg", raw=b"image", provider=provider)
    with patch.object(jobs, "complete_photo", delayed_complete):
      task = asyncio.create_task(jobs.run_photo_analysis(**kwargs))
      try:
        self.assertTrue(await asyncio.to_thread(entered.wait, 3))
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
      finally:
        finish.set()
      with self.assertRaises(asyncio.CancelledError):
        await task
    replay = await jobs.run_photo_analysis(**kwargs)
    self.assertEqual(replay["profile_json"]["analysis"], ANALYSIS)
    self.assertEqual(calls, [1])
    self.assertEqual(self.usage(), 1)
    self.assertEqual(self.engine.pool.checkedout(), 0)


if __name__ == "__main__":
  unittest.main()
