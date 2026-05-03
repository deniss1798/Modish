"""Загрузка переменных окружения из backend/.env (строго для секретов и БД)."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_backend_root = Path(__file__).resolve().parent.parent
_env_path = _backend_root / ".env"
load_dotenv(_env_path, encoding="utf-8")


def require_env(name: str) -> str:
  value = os.getenv(name)
  if value is None or not str(value).strip():
    hint = (
      f"Переменная {name!r} не задана или пуста.\n"
      f"Ожидается файл: {_env_path}\n"
      "В cmd из папки backend: copy .env.example .env\n"
      "Затем откройте .env в редакторе: задайте DATABASE_URL и JWT_SECRET (без кавычек, без пробелов вокруг =)."
    )
    if not _env_path.is_file():
      hint += f"\n(Сейчас файла .env по этому пути нет.)"
    raise RuntimeError(hint)
  return str(value).strip()


def get_database_url() -> str:
  return require_env("DATABASE_URL")


def get_jwt_secret() -> str:
  return require_env("JWT_SECRET")


def get_jwt_expires_hours() -> int:
  raw = os.getenv("JWT_EXPIRES_HOURS", "24").strip()
  return int(raw)


def allow_dev_analyze_bypass() -> bool:
  """Локальная отладка: не блокировать /analyze из‑за trial/Plus (только если явно включено в .env)."""
  return os.getenv("ALLOW_DEV_ANALYZE", "").strip().lower() in ("1", "true", "yes", "on")
