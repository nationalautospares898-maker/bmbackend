"""Structured logging with secret redaction.

`SECURITY.md §10`: passwords, PINs, tokens, cookies and `Authorization` headers must
never reach a log record. A redaction filter runs on every record rather than relying on
every call site remembering — and a test asserts a token-shaped string is scrubbed.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from contextvars import ContextVar
from typing import Any

#: Correlation id for the request being handled, read by every log record.
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
user_id_var: ContextVar[str | None] = ContextVar("user_id", default=None)
business_id_var: ContextVar[str | None] = ContextVar("business_id", default=None)
branch_id_var: ContextVar[str | None] = ContextVar("branch_id", default=None)

_REDACTED = "[REDACTED]"

# Patterns are deliberately broad: a false positive costs a little log readability,
# a false negative leaks a credential.
_SENSITIVE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # JWTs: three base64url segments.
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    # Bearer / Basic authorization headers.
    re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"),
    # key=value style secrets.
    re.compile(
        r"(?i)\b(password|passwd|pwd|pin|secret|token|api[_-]?key|pepper|signing[_-]?key"
        r"|authorization|cookie|set-cookie)\b\s*[:=]\s*(\"[^\"]*\"|'[^']*'|[^\s,;&}\]]+)"
    ),
    # Postgres URLs carrying a password.
    re.compile(r"(?i)\b(postgres(?:ql)?(?:\+\w+)?)://[^:\s]+:[^@\s]+@"),
)

_SENSITIVE_KEYS = frozenset(
    {
        "password",
        "new_password",
        "current_password",
        "passwd",
        "pwd",
        "pin",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "set-cookie",
        "jwt_signing_key",
        "password_pepper",
        "ip_hash_pepper",
        "password_hash",
        "pin_hash",
        "token_hash",
    }
)


def redact_text(value: str) -> str:
    """Scrub secret-shaped substrings from a free-text string."""
    for pattern in _SENSITIVE_PATTERNS:
        value = pattern.sub(
            lambda m: (
                f"{m.group(1)}://{_REDACTED}@"
                if m.lastindex and m.group(0).find("://") != -1
                else f"{m.group(1)}={_REDACTED}"
                if m.lastindex
                else _REDACTED
            ),
            value,
        )
    return value


def redact_value(value: Any, *, key: str | None = None) -> Any:
    """Recursively redact a structure before it is logged or sent to error reporting."""
    if key is not None and key.lower() in _SENSITIVE_KEYS:
        return _REDACTED
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {k: redact_value(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_value(v) for v in value]
    return value


class RedactionFilter(logging.Filter):
    """Applies redaction to the formatted message and to structured extras."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_text(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = redact_value(record.args)
            else:
                record.args = tuple(redact_value(a) for a in record.args)
        context = getattr(record, "context", None)
        if isinstance(context, dict):
            record.context = redact_value(context)
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line, with the request correlation fields attached."""

    _RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
        "message",
        "asctime",
        "taskName",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "msg": record.getMessage(),
            "logger": record.name,
        }
        for name, var in (
            ("request_id", request_id_var),
            ("user_id", user_id_var),
            ("business_id", business_id_var),
            ("branch_id", branch_id_var),
        ):
            value = var.get()
            if value:
                payload[name] = value
        for key, value in record.__dict__.items():
            if key not in self._RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(redact_value(payload), default=str, ensure_ascii=False)


def configure_logging(*, level: str = "INFO", fmt: str = "json") -> None:
    """Install the root handler. Idempotent, so repeated calls in tests are harmless."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RedactionFilter())
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s  %(message)s"))
    root.addHandler(handler)
    root.setLevel(level.upper())

    # Uvicorn's access log duplicates our own request log line.
    logging.getLogger("uvicorn.access").disabled = True
    logging.getLogger("uvicorn.error").propagate = True
