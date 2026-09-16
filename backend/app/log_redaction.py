"""Remove credential-bearing query parameters and provider keys from log records."""
import logging
import re


_QUERY = re.compile(r"([?&](?:code|access_code|access_token|api_key|token|user)=)[^&\s\"'<>]*", re.I)
_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")
_USERINFO = re.compile(r"(https?://)[^/\s@]+@", re.I)


def redact(value: str) -> str:
  value = _QUERY.sub(r"\1[REDACTED]", value)
  value = _KEY.sub("[REDACTED]", value)
  return _USERINFO.sub(r"\1[REDACTED]@", value)


class CredentialRedactionFilter(logging.Filter):
  def filter(self, record: logging.LogRecord) -> bool:
    record.msg = redact(record.getMessage())
    record.args = ()
    if record.exc_info:
      record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
    elif record.exc_text:
      record.exc_text = redact(record.exc_text)
    if record.stack_info:
      record.stack_info = redact(record.stack_info)
    return True


def install_log_redaction() -> None:
  for handler in logging.getLogger().handlers:
    if not any(isinstance(f, CredentialRedactionFilter) for f in handler.filters):
      handler.addFilter(CredentialRedactionFilter())
