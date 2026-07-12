"""fr-two-specific failure families and report -> packet context mapping."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .manifest import FrTwoManifest

# Adapter-owned failure families for fr-two.
FAMILY_MAP_FILTER = "map-filter"
FAMILY_REDLINING = "redlining"
FAMILY_AUTH = "auth"
FAMILY_BACKEND = "backend"
FAMILY_FRONTEND_BUILD = "frontend-build"
FAMILY_RENDERER = "renderer"
FAMILY_MAPPROXY = "mapproxy"
FAMILY_UNKNOWN = "unknown"

FR_TWO_FAMILIES = (
    FAMILY_MAP_FILTER,
    FAMILY_REDLINING,
    FAMILY_AUTH,
    FAMILY_BACKEND,
    FAMILY_FRONTEND_BUILD,
    FAMILY_RENDERER,
    FAMILY_MAPPROXY,
    FAMILY_UNKNOWN,
)


def fr_two_failure_family(
    spec_file: str, error_message: str, stack: str
) -> str:
    """Return the fr-two-specific failure family for a failure."""

    hay = f"{spec_file}\n{error_message}\n{stack}".lower()

    def has(*needles: str) -> bool:
        return any(n in hay for n in needles)

    # Domain families first (they refine what the generic classifier would call
    # an assertion/locator timeout).
    if has("map-filter", "map filter", "mapfilter", "filter panel"):
        return FAMILY_MAP_FILTER
    if has("redlin"):  # redlining / redline
        return FAMILY_REDLINING
    if has("mapproxy", "wmts", "tile cache", "/tiles/"):
        return FAMILY_MAPPROXY
    if has("qgis", "renderer", "map render", "ows"):
        return FAMILY_RENDERER
    if has("webpack", "vite", "module not found", "bundle", "compilation"):
        return FAMILY_FRONTEND_BUILD
    if has("401", "403", "unauthorized", "forbidden", "login", "auth"):
        return FAMILY_AUTH
    if has(
        "500", "internal server error", "traceback", "fastapi", "sqlalchemy"
    ):
        return FAMILY_BACKEND
    return FAMILY_UNKNOWN


def _first_failure(report: dict[str, Any]) -> tuple[str, str, str, str]:
    """Return (spec_file, title, error_message, stack) for the first failure."""

    def visit(node: dict[str, Any], titles: list[str]):
        for spec in node.get("specs", []) or []:
            title = " › ".join([*titles, str(spec.get("title", ""))])
            spec_file = str(spec.get("file") or node.get("file") or "")
            for test in spec.get("tests", []) or []:
                for result in test.get("results", []) or []:
                    errors = result.get("errors") or (
                        [result["error"]] if result.get("error") else []
                    )
                    if errors:
                        err = errors[0]
                        return (
                            spec_file,
                            title,
                            str(err.get("message", "")),
                            str(err.get("stack", "")),
                        )
        for child in node.get("suites", []) or []:
            child_title = str(child.get("title", ""))
            child_file = child.get("file")
            next_titles = (
                titles
                if child_file and child_title == str(child_file)
                else [*titles, child_title]
            )
            found = visit(child, next_titles)
            if found:
                return found
        return None

    found = visit({"suites": report.get("suites", [])}, [])
    return found or ("", "", "", "")


def map_fr_two_report_to_packet_context(
    report_path: Path,
    manifest: FrTwoManifest,
) -> dict[str, object]:
    """Return fr-two-specific report context for a failure packet."""

    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    spec_file, title, error_message, stack = _first_failure(report)
    family = fr_two_failure_family(spec_file, error_message, stack)

    slot = manifest.slots[0] if manifest.slots else {}
    return {
        "project_id": manifest.project_id,
        "spec_file": spec_file,
        "test_title": title,
        "error_message": error_message,
        "suspected_family": family,
        "slot_id": slot.get("id"),
        "database_name": slot.get("database_name"),
        "database_user": slot.get("database_user"),
    }
