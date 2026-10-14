"""Run fake-provider concurrency checks ONLY against an isolated PostgreSQL copy.

Requires DATABASE_URL in the process environment, an explicit host/database
assertion and --isolated-write-test. Never loads .env or contacts an AI provider.
The existing schema must already have migration 20261013_0025 applied.
"""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
import json
import logging
import os
from pathlib import Path
import secrets
import sys
import threading
import time
from unittest.mock import patch
from uuid import uuid4


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--isolated-write-test", action="store_true", required=True)
  parser.add_argument("--expected-host", required=True)
  parser.add_argument("--expected-database", required=True)
  parser.add_argument("--report", type=Path)
  args = parser.parse_args()
  from sqlalchemy import create_engine, delete, event, select, text
  from sqlalchemy.engine import make_url
  from sqlalchemy.orm import sessionmaker

  url = make_url(os.environ["DATABASE_URL"])
  if (url.get_backend_name() != "postgresql" or url.host != args.expected_host
      or url.database != args.expected_database or url.database in {"modish", "postgres"}
      or not any(marker in url.database.lower() for marker in ("test", "restore", "clone"))):
    raise ValueError("isolated_database_guard")
  logging.disable(logging.CRITICAL)
  source_root = Path('/app') if Path('/app/app').is_dir() else Path(__file__).resolve().parents[1] / 'backend'
  sys.path.insert(0, str(source_root))
  os.environ.update(APP_ENV="test", JWT_SECRET=secrets.token_urlsafe(48), PYTHON_DOTENV_DISABLED="1")
  with patch("dotenv.load_dotenv"):
    import httpx
    from app.api.deps import create_access_token, get_db
    from app.db import Base
    from app.main import app
    from app.models import PhotoAnalysisReservation, StyleProfile, User, UserLimits
    from app.services import photo_analysis_jobs as jobs

  engine = create_engine(url, pool_size=5, max_overflow=0,
                         connect_args={"options": "-c statement_timeout=8000 -c lock_timeout=5000"})
  sessions = sessionmaker(bind=engine, autoflush=False)
  with engine.connect() as connection:
    assert connection.scalar(text("SELECT current_database()")) == args.expected_database
    revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
    assert revision == "20261013_0025", "migration_required"
  fixture_ids: list[str] = []
  old_overrides = dict(app.dependency_overrides)
  def database():
    with sessions() as db:
      yield db
  app.dependency_overrides[get_db] = database
  analysis = {"summary": "Isolated provider fixture", "color_palette": ["navy"],
              "avoid_colors": [], "recommended_silhouettes": [], "recommended_items": [],
              "style_directions": []}
  pending = {"photo_analysis": analysis, "summary": analysis["summary"], "body_shape": "Rectangle"}

  async def race(path: str, target: str, result: dict, same_key: bool) -> dict:
    owner = str(uuid4())
    fixture_ids.append(owner)
    with sessions() as db:
      db.add(User(id=owner, email=f"photo-{uuid4().hex}@example.invalid", password_hash="unused",
                  subscription_status="active"))
      db.flush()
      db.add(StyleProfile(id=str(uuid4()), user_id=owner, profile_json={}))
      db.commit()
    token = create_access_token(owner)
    barrier = threading.Barrier(2, timeout=8)
    original_reserve = jobs.reserve_photo
    def simultaneous_reserve(*args):
      barrier.wait()
      return original_reserve(*args)
    entered, finish = asyncio.Event(), asyncio.Event()
    calls = 0
    async def provider(**kwargs):
      nonlocal calls
      calls += 1
      entered.set()
      await finish.wait()
      return deepcopy(result)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://isolated") as client:
      async def request(key):
        return await client.post(path, headers={"Authorization": f"Bearer {token}", "Idempotency-Key": key},
                                 files={"photo": ("fixture.jpg", b"isolated-image", "image/jpeg")})
      with patch.object(jobs, "reserve_photo", simultaneous_reserve), patch(target, provider):
        tasks = [asyncio.create_task(request("same")),
                 asyncio.create_task(request("same" if same_key else "other"))]
        try:
          await asyncio.wait_for(entered.wait(), 10)
          done, pending_tasks = await asyncio.wait(tasks, timeout=10, return_when=asyncio.FIRST_COMPLETED)
          assert len(done) == 1 and len(pending_tasks) == 1, "admission_did_not_resolve_while_provider_blocked"
          assert next(iter(done)).result().status_code == (409 if same_key else 403)
          assert calls == 1, "second_provider_invocation"
          assert engine.pool.checkedout() == 0, "transaction_retained_during_provider"
          with engine.connect() as connection:
            assert connection.scalar(text("SELECT 1")) == 1
        finally:
          finish.set()
          responses = await asyncio.gather(*tasks, return_exceptions=True)
      assert all(isinstance(response, httpx.Response) for response in responses), "request_failed"
      winner = next(response for response in responses if response.status_code == 200)
      winning_key = "same" if same_key or responses[0].status_code == 200 else "other"
      with patch(target, provider):
        replay = await request(winning_key)
      assert replay.status_code == 200 and replay.json() == winner.json()
      assert calls == 1, "replay_invoked_provider"
    with sessions() as db:
      assert db.scalar(select(UserLimits.photo_analysis_used).where(UserLimits.user_id == owner)) == 1
      records = db.scalars(select(PhotoAnalysisReservation).where(PhotoAnalysisReservation.user_id == owner)).all()
      assert len(records) == 1 and records[0].status == "completed"
    return {"status": "passed", "provider_calls": calls, "http_statuses": sorted(r.status_code for r in responses),
            "replay_status": replay.status_code, "checked_out_while_blocked": 0}

  async def registration_race() -> dict:
    email = f"register-{uuid4().hex}@example.com"
    barrier = threading.Barrier(2, timeout=10)
    def before_insert(mapper, connection, target):
      if target.email == email:
        barrier.wait()
    event.listen(User, "before_insert", before_insert)
    try:
      async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://isolated") as client:
        password = secrets.token_urlsafe(24)
        results = await asyncio.gather(*[
          client.post("/auth/register", json={"email": email, "password": password}) for _ in range(2)
        ])
      assert sorted(response.status_code for response in results) == [200, 409]
      assert next(response for response in results if response.status_code == 200).json()["refresh_token"]
      with sessions() as db:
        assert len(db.scalars(select(User.id).where(User.email == email)).all()) == 1
      return {"status": "passed", "accounts": 1, "http_statuses": [200, 409]}
    finally:
      event.remove(User, "before_insert", before_insert)
      with sessions() as db:
        fixture_ids.extend(db.scalars(select(User.id).where(User.email == email)).all())

  async def checks():
    report = {}
    for name, path, target, result in (
      ("onboarding", "/onboarding/photo", "app.api.onboarding.analyze_onboarding_photo", pending),
      ("style_profile", "/style-profile/analyze", "app.api.profile.analyze_photo_ai", analysis),
    ):
      report[name + "_one_remaining_slot"] = await race(path, target, result, False)
      report[name + "_duplicate_key"] = await race(path, target, result, True)
    report["same_email_registration"] = await registration_race()
    return report

  started = time.perf_counter()
  try:
    with patch.object(jobs, "SessionLocal", sessions):
      report = asyncio.run(checks())
  finally:
    app.dependency_overrides.clear()
    app.dependency_overrides.update(old_overrides)
    try:
      # Only UUID owners created above; restored catalogue/user rows stay intact.
      with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
          if "user_id" in table.c:
            connection.execute(delete(table).where(table.c.user_id.in_(fixture_ids)))
        connection.execute(delete(User).where(User.id.in_(fixture_ids)))
    finally:
      engine.dispose()
  output = {"status": "passed", "database": "isolated_postgresql", "revision": revision,
            "seconds": round(time.perf_counter() - started, 3), "checks": report,
            "fixture_cleanup": "committed", "external_provider_calls": 0}
  if args.report:
    args.report.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
  print(json.dumps(output))
  return 0


if __name__ == "__main__":
  try:
    raise SystemExit(main())
  except Exception as exc:
    import traceback
    # Locations make failed isolated checks diagnosable without exposing SQL
    # parameters, response bodies, JWTs or connection strings.
    frames = [{'file': Path(frame.filename).name, 'line': frame.lineno,
               'function': frame.name} for frame in traceback.extract_tb(exc.__traceback__)]
    code = str(exc) if isinstance(exc, AssertionError) and str(exc).replace('_', '').isalnum() else None
    print(json.dumps({"status": "failed", "error_type": type(exc).__name__,
                      'check': code, 'locations': frames[-12:]}))
    raise SystemExit(1)
