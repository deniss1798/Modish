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


def get_openai_api_key() -> str:
  return require_env("OPENAI_API_KEY")


def get_ai_api_key() -> str:
  """
  Универсальный ключ для LLM-провайдера.

  Приоритет:
  - AI_API_KEY (для DeepSeek / других OpenAI-compatible провайдеров)
  - OPENAI_API_KEY (обратная совместимость со старой настройкой)
  """
  value = (os.getenv("AI_API_KEY") or "").strip()
  if value:
    return value
  return get_openai_api_key()


def get_ai_base_url() -> str:
  # Пример для совместимых провайдеров: https://api.deepseek.com
  return os.getenv("AI_BASE_URL", "https://api.openai.com").strip() or "https://api.openai.com"


def get_ai_mode() -> str:
  """
  Режим API:
  - responses: OpenAI Responses API (/v1/responses)
  - chat: OpenAI-compatible Chat Completions (/v1/chat/completions)
  """
  return os.getenv("AI_MODE", "responses").strip().lower() or "responses"


def get_ai_responses_path() -> str:
  return os.getenv("AI_RESPONSES_PATH", "/v1/responses").strip() or "/v1/responses"


def get_ai_chat_completions_path() -> str:
  return os.getenv("AI_CHAT_COMPLETIONS_PATH", "/v1/chat/completions").strip() or "/v1/chat/completions"


def get_style_analysis_model() -> str:
  value = (os.getenv("STYLE_ANALYSIS_MODEL") or "").strip()
  if value:
    return value
  # Автодефолты под временные провайдеры, чтобы не ловить 400 на несовместимых моделях.
  base = get_ai_base_url().lower()
  if "deepseek" in base:
    return "deepseek-v4-flash"
  return "gpt-4.1-mini"


def get_visual_analysis_text_model() -> str:
  value = (os.getenv("VISUAL_ANALYSIS_TEXT_MODEL") or "").strip()
  if value:
    return value
  base = get_ai_base_url().lower()
  if "deepseek" in base:
    return "deepseek-v4-flash"
  return "gpt-4.1-mini"


def get_visual_analysis_image_model() -> str:
  return os.getenv("VISUAL_ANALYSIS_IMAGE_MODEL", "gpt-image-2").strip() or "gpt-image-2"
