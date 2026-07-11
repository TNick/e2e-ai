"""Collect screenshot/trace attachments and the error-context.md file."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..inventory.models import DiscoveredTest

_SCREENSHOT_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
_TRACE_HINTS = ("trace", "blob")


def _iter_results(report: Mapping[str, object]):
    """Yield every result dict in a Playwright JSON report."""

    def visit(node: dict[str, Any]):
        for spec in node.get("specs", []) or []:
            for test in spec.get("tests", []) or []:
                yield from test.get("results", []) or []
        for child in node.get("suites", []) or []:
            yield from visit(child)

    yield from visit({"suites": report.get("suites", [])})


def _resolve(path_str: str, work_dir: Path) -> Path:
    path = Path(path_str)
    return path if path.is_absolute() else (work_dir / path)


def extract_attachment_paths(
    report: Mapping[str, object],
    work_dir: Path,
) -> tuple[list[Path], list[Path]]:
    """Return (screenshot_paths, trace_paths) from a Playwright report."""

    screenshots: list[Path] = []
    traces: list[Path] = []
    seen: set[str] = set()

    for result in _iter_results(report):
        for att in result.get("attachments", []) or []:
            raw = att.get("path")
            if not raw:
                continue
            resolved = _resolve(str(raw), work_dir)
            key = str(resolved)
            if key in seen:
                continue
            seen.add(key)
            name = str(att.get("name", "")).lower()
            content_type = str(att.get("contentType", "")).lower()
            suffix = resolved.suffix.lower()
            is_image = (
                content_type.startswith("image/")
                or suffix in _SCREENSHOT_EXTS
                or "screenshot" in name
            )
            if is_image:
                screenshots.append(resolved)
            elif suffix == ".zip" or any(h in name for h in _TRACE_HINTS):
                traces.append(resolved)
            else:
                traces.append(resolved)
    return screenshots, traces


def find_error_context(work_dir: Path, test: DiscoveredTest) -> Path | None:
    """Find the most relevant Playwright ``error-context.md`` for the attempt.

    Playwright writes ``error-context.md`` into the per-test results directory.
    We search the attempt work directory tree and prefer a file whose path hints
    at this test's spec file.
    """

    if not work_dir.is_dir():
        return None
    candidates = sorted(work_dir.rglob("error-context.md"))
    if not candidates:
        return None
    stem = Path(test.spec_file).stem.lower()
    for candidate in candidates:
        if stem and stem in str(candidate).lower():
            return candidate
    return candidates[0]
