"""Short database transactions around an external photo analysis operation.

The owner row serializes admission, refunds and completion. A dead worker's
expired reservation stays charged: expiry alone cannot prove the provider stopped.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import re
from typing import Any, Awaitable, Callable
from uuid import uuid4

from anyio import CancelScope
from anyio.lowlevel import checkpoint
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import func, select

from .. import business
from ..db import SessionLocal
from ..models import PhotoAnalysisReservation, StyleProfile, User, UserLimits

PHOTO_PROVIDER_TIMEOUT_SECONDS = 180
PHOTO_RESERVATION_TTL_SECONDS = 300


def _now() -> datetime:
  return datetime.now(timezone.utc)


def _utc(value: datetime) -> datetime:
  return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


@dataclass(frozen=True)
class ReservedPhoto:
  id: str
  user_id: str
  replay: dict[str, Any] | None = None


def photo_request_id(value: str | None) -> str:
  if value is None:
    return str(uuid4())
  if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", value):
    raise HTTPException(status_code=400, detail="Некорректный Idempotency-Key.")
  return value


def _owner(db, credentials):
  from ..api.deps import user_from_token

  user = user_from_token(credentials, db)
  return db.execute(
    select(User).where(User.id == user.id).with_for_update().execution_options(populate_existing=True)
  ).scalar_one()


def reserve_photo(credentials, request_id: str, kind: str, content_digest: str) -> ReservedPhoto:
  """Return only detached values, after the quota debit has been committed."""
  if kind not in {"onboarding", "style_profile"}:
    raise ValueError("unknown_photo_operation")
  with SessionLocal() as db:
    user = _owner(db, credentials)
    now = _now()
    existing = db.execute(select(PhotoAnalysisReservation).where(
      PhotoAnalysisReservation.user_id == user.id,
      PhotoAnalysisReservation.request_id == request_id,
    )).scalar_one_or_none()
    if existing is not None:
      if existing.kind != kind or existing.content_digest != content_digest:
        raise HTTPException(status_code=409, detail="Этот запрос уже использован для другого фото.")
      if existing.status == "completed":
        return ReservedPhoto(existing.id, user.id, deepcopy(existing.response_json))
      if existing.status == "pending" and _utc(existing.expires_at) <= now:
        existing.status = "expired"
        existing.finished_at = now
        db.commit()
      if existing.status == "pending":
        raise HTTPException(status_code=409, detail="Анализ уже выполняется. Подождите и повторите запрос.",
                            headers={"Retry-After": "2"})
      if existing.status == "expired":
        raise HTTPException(status_code=409, detail="Результат анализа не подтверждён. Повторный запуск временно недоступен.")
      raise HTTPException(status_code=409, detail="Анализ завершён без результата. Загрузите фото повторно.")

    if not business.is_plus_available(user, now):
      raise HTTPException(status_code=403, detail="Анализ фото сейчас недоступен. Продолжите подбор без фото или повторите позже.")
    limits = business.get_or_refresh_limits(db, user.id, now=now)
    if limits.photo_analysis_used >= business.PLUS_MONTHLY_PHOTO_ANALYSES:
      raise HTTPException(status_code=403, detail="Лимит анализа фото на этот месяц исчерпан. Продолжите подбор без фото.")
    generation = 1 + int(db.scalar(select(func.max(PhotoAnalysisReservation.generation)).where(
      PhotoAnalysisReservation.user_id == user.id,
    )) or 0)
    row = PhotoAnalysisReservation(
      id=str(uuid4()), user_id=user.id, request_id=request_id, kind=kind,
      content_digest=content_digest, generation=generation, period_start=limits.period_start,
      status="pending", created_at=now,
      expires_at=now + timedelta(seconds=PHOTO_RESERVATION_TTL_SECONDS),
    )
    limits.photo_analysis_used += 1
    limits.updated_at = now
    db.add(row)
    result = ReservedPhoto(row.id, user.id)
    db.commit()
    return result


def release_photo(reservation: ReservedPhoto, status: str = "failed") -> None:
  """Refund only this stopped operation, and only its original quota period."""
  if status not in {"failed", "cancelled"}:
    raise ValueError("invalid_photo_release_status")
  with SessionLocal() as db:
    db.execute(select(User.id).where(User.id == reservation.user_id).with_for_update()).scalar_one()
    row = db.get(PhotoAnalysisReservation, reservation.id)
    if row is None or row.user_id != reservation.user_id or row.status not in {"pending", "expired"}:
      return
    limits = db.execute(select(UserLimits).where(UserLimits.user_id == row.user_id)).scalar_one()
    if _utc(limits.period_start) == _utc(row.period_start):
      limits.photo_analysis_used = max(0, limits.photo_analysis_used - 1)
      limits.updated_at = _now()
    row.status = status
    row.finished_at = _now()
    db.commit()


def complete_photo(reservation: ReservedPhoto, analysis: dict[str, Any]) -> dict[str, Any]:
  """Persist one result; a late provider can never replace a newer request."""
  from ..schemas.photo_analysis import build_profile_json_after_analysis
  from .onboarding_service import apply_photo_analysis_pending
  from .profile_repository import ensure_style_profile

  with SessionLocal() as db:
    db.execute(select(User.id).where(User.id == reservation.user_id).with_for_update()).scalar_one()
    row = db.get(PhotoAnalysisReservation, reservation.id)
    if row is None or row.user_id != reservation.user_id:
      raise HTTPException(status_code=409, detail="Запрос анализа недоступен.")
    if row.status == "completed":
      return deepcopy(row.response_json)
    now = _now()
    if row.status != "pending":
      raise HTTPException(status_code=409, detail="Этот запрос анализа больше не активен.")
    latest = db.scalar(select(func.max(PhotoAnalysisReservation.generation)).where(
      PhotoAnalysisReservation.user_id == row.user_id,
    ))
    if latest != row.generation or _utc(row.expires_at) <= now:
      row.status = "superseded" if latest != row.generation else "expired"
      row.finished_at = now
      db.commit()
      raise HTTPException(status_code=409, detail="Результат устарел. Откройте текущий профиль.")
    profile = ensure_style_profile(db, row.user_id)
    if row.kind == "onboarding":
      result = apply_photo_analysis_pending(db, profile=profile, pending=deepcopy(analysis))
    else:
      profile.profile_json = build_profile_json_after_analysis(analysis, source="photo_analysis_v2_openai")
      profile.confidence_score = 0.75
      profile.updated_at = now
      db.flush()
      business.rebuild_user_summary(db, row.user_id)
      result = {
        "id": profile.id, "user_id": profile.user_id, "style_target": profile.style_target,
        "confidence_score": profile.confidence_score, "profile_json": deepcopy(profile.profile_json),
      }
    row.response_json = deepcopy(result)
    row.status = "completed"
    row.finished_at = now
    db.commit()
    return result


async def _wait_short_task(task):
  """Defer repeated native Task.cancel as well as AnyIO scope cancellation."""
  cancelled = False
  with CancelScope(shield=True):
    while True:
      try:
        return await asyncio.shield(task), cancelled
      except asyncio.CancelledError:
        # The helper owns this task; do not spin if the worker task itself was
        # cancelled (for example during final event-loop shutdown).
        if task.cancelled():
          raise
        cancelled = True


async def _short_call(function, *args):
  # Worker sessions always close/commit before cancellation returns to ASGI.
  task = asyncio.create_task(asyncio.to_thread(function, *args))
  result, cancelled = await _wait_short_task(task)
  if cancelled:
    raise asyncio.CancelledError
  return result


async def run_photo_analysis(
  *, credentials: HTTPAuthorizationCredentials | None, request_id: str | None,
  kind: str, content_type: str, raw: bytes,
  provider: Callable[..., Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
  if not raw:
    raise HTTPException(status_code=400, detail="Фото пустое.")
  key = photo_request_id(request_id)
  digest = sha256(content_type.encode("utf-8") + b"\0" + raw).hexdigest()
  admission = asyncio.create_task(asyncio.to_thread(reserve_photo, credentials, key, kind, digest))
  reservation, cancelled = await _wait_short_task(admission)
  if cancelled:
    try:
      if reservation.replay is None:
        await _short_call(release_photo, reservation, "cancelled")
    finally:
      raise asyncio.CancelledError
  if reservation.replay is not None:
    return reservation.replay
  try:
    # Observe an enclosing ASGI cancellation deferred by admission before even
    # constructing/scheduling the provider coroutine.
    await checkpoint()
    result = await asyncio.wait_for(
      provider(content_type=content_type, raw=raw), timeout=PHOTO_PROVIDER_TIMEOUT_SECONDS,
    )
  except asyncio.CancelledError:
    await _short_call(release_photo, reservation, "cancelled")
    raise
  except Exception as exc:
    await _short_call(release_photo, reservation, "failed")
    if isinstance(exc, TimeoutError):
      raise HTTPException(status_code=504, detail="Анализ занял слишком много времени. Попробуйте ещё раз.") from exc
    # Provider errors can contain response payloads/URLs; never echo those.
    raise HTTPException(status_code=502, detail="Не удалось выполнить анализ фото. Попробуйте ещё раз.") from exc
  # Completion failures keep the debit: its commit may have succeeded, and the
  # same request ID can safely replay a committed result without another call.
  return await _short_call(complete_photo, reservation, result)


def visual_analysis_snapshot(credentials) -> dict[str, Any]:
  from ..api.deps import user_from_token

  with SessionLocal() as db:
    user = user_from_token(credentials, db)
    profile = db.execute(select(StyleProfile).where(StyleProfile.user_id == user.id)).scalar_one_or_none()
    data = profile.profile_json if profile is not None else None
    analysis = data.get("analysis") if isinstance(data, dict) else None
    if not isinstance(analysis, dict) or not analysis:
      raise HTTPException(status_code=400, detail="Сначала выполните анализ стиля по фото.")
    return deepcopy(analysis)


async def detached_visual_analysis(credentials) -> dict[str, Any]:
  return await _short_call(visual_analysis_snapshot, credentials)
