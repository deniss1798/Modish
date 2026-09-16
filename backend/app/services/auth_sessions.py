"""Revocable device sessions. Only a hash of the opaque refresh token is stored."""
from datetime import datetime, timedelta, timezone
import hashlib
import secrets
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select

from ..models import AuthSession


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_session(db, user_id: str) -> str:
    token = secrets.token_urlsafe(48)
    db.add(AuthSession(id=str(uuid4()), user_id=user_id, token_hash=token_hash(token),
        expires_at=datetime.now(timezone.utc) + timedelta(days=90)))
    return token


def valid_session(db, token: str):
    session = db.execute(select(AuthSession).where(
        AuthSession.token_hash == token_hash(token),
        AuthSession.expires_at > datetime.now(timezone.utc),
        AuthSession.revoked_at.is_(None))).scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=401, detail='Сессия истекла. Войдите снова.')
    return session
