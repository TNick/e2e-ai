"""Text normalization and secret redaction for failure evidence."""

from __future__ import annotations

import re

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_TIMESTAMP_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?"
    r"(?:Z|[+-]\d{2}:?\d{2})?\b"
)
# Volatile ephemeral ports (>= 1024) that appear right after a host/colon.
_PORT_RE = re.compile(r"(?<=:)\d{4,5}\b")
_HEX_ID_RE = re.compile(r"\b[0-9a-f]{16,}\b", re.IGNORECASE)

# Keys/patterns whose values are likely secret.
_SECRET_KEY_RE = re.compile(
    r"(password|passwd|token|secret|api[_-]?key|authorization|auth|"
    r"set-cookie|cookie|private[_-]?key|credential|session)",
    re.IGNORECASE,
)
# Inline "key=value" / "key: value" secret assignments in free text.
_SECRET_ASSIGN_RE = re.compile(
    r"(?i)\b(password|passwd|token|secret|api[_-]?key|authorization|"
    r"cookie|private[_-]?key|credential)\b\s*[=:]\s*([^\s,;\"']+)"
)
_BEARER_RE = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-]+")

REDACTED = "***redacted***"


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences."""

    return _ANSI_RE.sub("", text or "")


def normalize_error_text(text: str | None) -> str:
    """Normalize whitespace and volatile values in error text.

    Strips ANSI, replaces timestamps, volatile ports, and long hex ids with
    placeholders, and collapses trailing whitespace. URLs, test names, and
    source locations are intentionally preserved.
    """

    if not text:
        return ""
    result = strip_ansi(text)
    result = _TIMESTAMP_RE.sub("<ts>", result)
    result = _PORT_RE.sub("<port>", result)
    result = _HEX_ID_RE.sub("<hex>", result)
    # Collapse runs of spaces/tabs but keep line structure.
    collapsed = (
        re.sub(r"[ \t]+", " ", line).rstrip() for line in result.splitlines()
    )
    return "\n".join(collapsed).strip()


def tail_lines(text: str, limit: int = 80) -> list[str]:
    """Return the last relevant (non-blank) lines from a log."""

    if not text:
        return []
    lines = [line.rstrip() for line in strip_ansi(text).splitlines()]
    non_blank = [line for line in lines if line.strip()]
    return non_blank[-limit:]


def redact_sensitive_text(text: str) -> str:
    """Redact likely secrets from failure context text."""

    if not text:
        return text
    result = _SECRET_ASSIGN_RE.sub(lambda m: f"{m.group(1)}={REDACTED}", text)
    result = _BEARER_RE.sub(lambda m: f"{m.group(1)} {REDACTED}", result)
    return result


def redact_sensitive_json(data: object) -> object:
    """Recursively redact likely secrets from JSON-compatible data."""

    if isinstance(data, dict):
        redacted: dict[object, object] = {}
        for key, value in data.items():
            if isinstance(key, str) and _SECRET_KEY_RE.search(key):
                redacted[key] = REDACTED
            else:
                redacted[key] = redact_sensitive_json(value)
        return redacted
    if isinstance(data, (list, tuple)):
        return [redact_sensitive_json(item) for item in data]
    if isinstance(data, str):
        return redact_sensitive_text(data)
    return data
