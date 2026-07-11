"""MCP artifact listing and redaction."""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

from ..analysis.text import redact_sensitive_text

_ARTIFACT_SUFFIXES = (
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".json",
    ".txt",
    ".log",
    ".har",
)


def list_mcp_artifacts(output_dir: Path) -> list[Path]:
    """Return files produced by one MCP session."""

    if not output_dir.is_dir():
        return []
    paths: list[Path] = []
    for path in sorted(output_dir.rglob("*")):
        if path.is_file():
            paths.append(path)
    return paths


def summarize_mcp_artifacts(paths: Sequence[Path]) -> dict[str, object]:
    """Return artifact counts, sizes, and notable files."""

    total_bytes = 0
    by_suffix: dict[str, int] = {}
    notable: list[str] = []
    for path in paths:
        try:
            size = path.stat().st_size
        except OSError:
            continue
        total_bytes += size
        suffix = path.suffix.lower() or "(none)"
        by_suffix[suffix] = by_suffix.get(suffix, 0) + 1
        if path.suffix.lower() in _ARTIFACT_SUFFIXES and len(notable) < 20:
            notable.append(str(path))
    return {
        "count": len(paths),
        "total_bytes": total_bytes,
        "by_suffix": by_suffix,
        "notable_paths": notable,
    }


def redact_mcp_artifact_text(text: str) -> str:
    """Redact sensitive text found in MCP artifacts."""

    cleaned = redact_sensitive_text(text)
    cleaned = re.sub(
        r"(?i)(authorization|cookie|token|password)\s*[:=]\s*\S+",
        r"\1: [redacted]",
        cleaned,
    )
    return cleaned
