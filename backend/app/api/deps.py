"""Общие зависимости FastAPI: БД, JWT, пароли."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_jwt_expires_hours, get_jwt_secret
from ..db import SessionLocal
from ..models import User

auth_scheme = HTTPBearer(auto_error=False)
JWT_SECRET = get_jwt_secret()
JWT_ALG = "HS256"
JWT_EXPIRES_HOURS = get_jwt_expires_hours()


def get_db():
  db = SessionLocal()
  try:
    yield db
  finally:
    db.close()


def user_from_token(
  credentials: HTTPAuthorizationCredentials | None,
  db: Session,
) -> User:
  if credentials is None or credentials.scheme.lower() != "bearer":
    raise HTTPException(
      status_code=status.HTTP_401_UNAUTHORIZED,
      detail="Missing bearer token",
    )
  try:
    payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALG])
  except jwt.PyJWTError as exc:
    raise HTTPException(
      status_code=status.HTTP_401_UNAUTHORIZED,
      detail="Invalid token",
    ) from exc
  user_id = payload.get("sub")
  if not user_id:
    raise HTTPException(
      status_code=status.HTTP_401_UNAUTHORIZED,
      detail="Invalid token payload",
    )
  user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
  if user is None:
    raise HTTPException(
      status_code=status.HTTP_401_UNAUTHORIZED,
      detail="User not found",
    )
  return user


def hash_password(password: str) -> str:
  raw = password.encode("utf-8")
  if len(raw) > 72:
    raise HTTPException(status_code=400, detail="Пароль слишком длинный (bcrypt: макс. 72 байта)")
  return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("ascii")


def verify_password(plain: str, hashed: str) -> bool:
  try:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("ascii"))
  except (ValueError, TypeError):
    return False


def create_access_token(user_id: str) -> str:
  now = datetime.now(timezone.utc)
  payload = {
    "sub": user_id,
    "iat": int(now.timestamp()),
    "exp": int((now + timedelta(hours=JWT_EXPIRES_HOURS)).timestamp()),
  }
  return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)
