"""Optional exact-email admission for a small, explicitly managed closed alpha.

Policy comparison trims and case-folds email addresses. It never renames an
account, collapses dots/plus aliases, accepts wildcard domains or logs emails.
Current process settings are reread on each check; no file watcher is implied.
"""
from fastapi import HTTPException
from pydantic import EmailStr, TypeAdapter

from ..config import get_closed_alpha_config

ALPHA_ACCESS_MESSAGE = 'Доступ к Modish пока открыт только участникам закрытого тестирования. Свяжитесь с командой, чтобы получить доступ.'
MAX_ALPHA_EMAILS = 50
MAX_ALPHA_EMAIL_LENGTH = 254
_OFF_VALUES = frozenset({'false', '0'})
_ON_VALUES = frozenset({'true', '1'})
_EMAIL = TypeAdapter(EmailStr)


def _email_key(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= MAX_ALPHA_EMAIL_LENGTH
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
            or any(character in value for character in '<>')):
        return None
    try:
        validated = str(_EMAIL.validate_python(value.strip()))
    except (ValueError, TypeError):
        return None
    return validated.casefold() if len(validated) <= MAX_ALPHA_EMAIL_LENGTH else None


def alpha_access_required():
    raw_enabled, _ = get_closed_alpha_config()
    # A misspelled or blank flag must not silently open access.
    return raw_enabled.strip().lower() not in _OFF_VALUES


def require_alpha_access(email):
    raw_enabled, raw_emails = get_closed_alpha_config()
    enabled = raw_enabled.strip().lower()
    if enabled in _OFF_VALUES:
        return
    allowed = set()
    if (enabled in _ON_VALUES and raw_emails
            and len(raw_emails) <= MAX_ALPHA_EMAILS * (MAX_ALPHA_EMAIL_LENGTH + 1)
            and not any(ord(character) < 32 or ord(character) == 127 for character in raw_emails)):
        entries = raw_emails.split(',')
        if len(entries) <= MAX_ALPHA_EMAILS:
            for entry in entries:
                key = _email_key(entry.strip())
                if key is None:
                    allowed.clear()
                    break
                allowed.add(key)
    key = _email_key(email)
    if key is None or key not in allowed:
        raise HTTPException(status_code=403, detail=ALPHA_ACCESS_MESSAGE)
