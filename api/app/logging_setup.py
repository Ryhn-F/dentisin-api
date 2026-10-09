"""Structured JSON logging WITHOUT PII, photos, storage paths or tokens.

Defence in depth:
1. Call sites log fixed event names plus a small allowlist of scalar fields.
2. ``JsonFormatter`` only emits allowlisted ``extra`` keys (anything else is
   dropped), never ``exc_info`` text/tracebacks.
3. ``redact()`` scrubs bearer tokens, JWT-shaped strings, Supabase keys,
   storage object paths and UUIDs from every emitted string.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Final

ALLOWED_EXTRA: Final = frozenset(
    {
        "request_id",
        "method",
        "route",
        "status",
        "duration_ms",
        "event",
        "exc_type",
        "code",
        "count",
        "role",
        "enabled",
        "upstream_status",
    }
)

_REDACTIONS: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer [REDACTED]"),
    # JWT: three base64url segments, first starts with eyJ ({"...)
    (re.compile(r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*"), "[JWT]"),
    (re.compile(r"sb_(?:secret|publishable)_[A-Za-z0-9_-]+"), "[SUPABASE_KEY]"),
    # dataset_photos object path: <uid>/<child>/<uuid>.jpg
    (re.compile(r"[0-9a-fA-F-]{36}/[0-9a-fA-F-]{36}/[0-9a-fA-F-]{36}\.jpe?g"), "[OBJECT_PATH]"),
    (
        re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"),
        "[UUID]",
    ),
)


def redact(text: str) -> str:
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        for key in ALLOWED_EXTRA:
            if key in record.__dict__:
                value = record.__dict__[key]
                if isinstance(value, str):
                    value = redact(value)
                elif not isinstance(value, int | float | bool) and value is not None:
                    value = redact(str(value))
                payload[key] = value
        if record.exc_info and record.exc_info[0] is not None:
            payload["exc_type"] = record.exc_info[0].__name__
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def configure_logging(level: str = "INFO") -> None:
    """Route every logger (incl. uvicorn's) through the JSON formatter."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers[:] = []
        lg.propagate = True
    # uvicorn's access log prints raw paths + query strings: disable it, our
    # middleware logs the route TEMPLATE instead.
    access = logging.getLogger("uvicorn.access")
    access.handlers[:] = []
    access.propagate = False
    access.disabled = True
    # httpx logs full URLs (which could contain object paths) at INFO.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
