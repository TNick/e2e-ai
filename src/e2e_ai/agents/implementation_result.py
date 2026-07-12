"""Parse structured implementer agent output."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_JSON_OBJECT_RE = re.compile(r"\{[^{}]*\}")


@dataclass(frozen=True)
class ImplementationResult:
    """Subset of the implementer structured output schema."""

    summary: str = ""
    changed_files: tuple[str, ...] = ()
    runtime_refresh_actions: tuple[str, ...] = ()


def _coerce_string_list(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    items: list[str] = []
    for entry in value:
        if isinstance(entry, str) and entry.strip():
            items.append(entry.strip())
    return tuple(items)


def parse_implementation_result(text: str) -> ImplementationResult | None:
    """Extract structured implementation output from agent stdout."""

    stripped = text.strip()
    if not stripped:
        return None

    candidates: list[str] = []
    for line in reversed(stripped.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            candidates.append(line)
    candidates.append(stripped)

    for candidate in candidates:
        payload: object | None = None
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            for match in reversed(list(_JSON_OBJECT_RE.finditer(candidate))):
                try:
                    payload = json.loads(match.group(0))
                except json.JSONDecodeError:
                    payload = None
                    continue
                break
        if not isinstance(payload, dict):
            continue
        if "changed_files" not in payload and "runtime_refresh_actions" not in (
            payload
        ):
            continue
        return ImplementationResult(
            summary=str(payload.get("summary", "")),
            changed_files=_coerce_string_list(payload.get("changed_files")),
            runtime_refresh_actions=_coerce_string_list(
                payload.get("runtime_refresh_actions")
            ),
        )

    logger.log(1, "could not parse structured implementation result")
    return None
