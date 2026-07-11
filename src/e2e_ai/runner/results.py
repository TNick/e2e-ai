"""Parse Playwright JSON reporter output into statuses and failure context."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..models import FailureInfo
from .models import STATUS_FAILED, STATUS_PASSED


def load_playwright_json(path: Path) -> dict[str, object]:
    """Load a Playwright JSON reporter file (empty dict when missing/invalid)."""

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def summarize_playwright_json(data: Mapping[str, object]) -> dict[str, int]:
    """Return passed, failed, skipped, expected, and unexpected counts."""

    stats = data.get("stats") or {}
    if not isinstance(stats, Mapping):
        stats = {}

    def _int(key: str) -> int:
        value = stats.get(key, 0)
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    expected = _int("expected")
    unexpected = _int("unexpected")
    return {
        "expected": expected,
        "unexpected": unexpected,
        "skipped": _int("skipped"),
        "flaky": _int("flaky"),
        "passed": expected,
        "failed": unexpected,
    }


def attempt_status_from_report(
    exit_code: int,
    data: Mapping[str, object] | None,
) -> str:
    """Return a normalized attempt status from a report + exit code.

    Timeouts, interruptions, and launch failures are decided by the caller from
    the process outcome; this function only distinguishes passed vs. failed once
    Playwright produced (or failed to produce) a report.
    """

    if data:
        summary = summarize_playwright_json(data)
        if summary["unexpected"] > 0:
            return STATUS_FAILED
        if summary["expected"] >= 1:
            return STATUS_PASSED
        # No expected tests ran (e.g. all skipped). Trust the exit code.
        return STATUS_PASSED if exit_code == 0 else STATUS_FAILED
    return STATUS_PASSED if exit_code == 0 else STATUS_FAILED


def _tail(text: str, limit: int = 4000) -> str:
    text = text or ""
    return text if len(text) <= limit else "…(truncated)…\n" + text[-limit:]


def extract_failure(
    data: Mapping[str, object] | None,
    log_text: str,
) -> FailureInfo:
    """Pull the first error out of a Playwright JSON report.

    ``log_text`` is the combined stdout/stderr, used both as a tail on the
    returned packet and as a fallback when the report has no structured error.
    """

    info = FailureInfo(stdout_tail=_tail(log_text))

    def visit(node: dict[str, Any]) -> bool:
        for spec in node.get("specs", []) or []:
            for test in spec.get("tests", []) or []:
                for result in test.get("results", []) or []:
                    errors = result.get("errors") or (
                        [result["error"]] if result.get("error") else []
                    )
                    if result.get("status") in ("passed", "skipped") and not errors:
                        continue
                    if errors:
                        err = errors[0]
                        info.error_message = str(err.get("message", "")).strip()[:8000]
                        info.stack = str(err.get("stack", "")).strip()[:8000]
                        loc = err.get("location") or {}
                        if loc:
                            info.location = (
                                f"{loc.get('file', '')}:{loc.get('line', '')}"
                                f":{loc.get('column', '')}"
                            )
                        info.duration_ms = result.get("duration")
                        for att in result.get("attachments", []) or []:
                            path = att.get("path")
                            if path:
                                info.attachments.append(str(path))
                        return True
        for child in node.get("suites", []) or []:
            if visit(child):
                return True
        return False

    if data:
        visit({"suites": data.get("suites", [])})
    if not info.error_message:
        info.error_message = "test failed (no structured error in report)"
    return info
