"""Parse Playwright list output into discovered tests."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
from typing import TYPE_CHECKING, Any

from ..errors import CatalogError
from .models import DiscoveredTest, TestInventory

if TYPE_CHECKING:
    # Type-only import: importing at runtime creates a config -> mcp -> analysis
    # -> inventory -> config import cycle.
    from ..config.models import EffectiveConfig

logger = logging.getLogger(__name__)

_JSON_REPORTER_ARGS = ("--reporter=json",)
_LIST_LINE_RE = re.compile(
    r"^(?:\[(?P<project>[^\]]+)\]\s*[›>]\s*)?"
    r"(?P<file>[^›>\n]+?)"
    r"(?:\s*[›>]\s*(?P<title>.+))?\s*$"
)
_LINE_COL_RE = re.compile(r"^(?P<file>.+?):\d+(?::\d+)?(?:\s*[›>]\s*|$)")


def _short_project_id(project_id: str) -> str:
    cleaned = (
        re.sub(r"[^a-zA-Z0-9-]+", "-", project_id.strip()).strip("-").lower()
    )
    if not cleaned:
        cleaned = "project"
    return cleaned[:24]


def build_test_id(
    project_id: str,
    spec_file: str,
    title: str,
    project_name: str | None,
    *,
    explicit_test_id: str | None = None,
) -> str:
    """Return a stable id for one test."""

    prefix = _short_project_id(project_id)
    normalized_file = spec_file.replace("\\", "/")
    if explicit_test_id:
        payload = f"explicit:{explicit_test_id}::project:{project_name or ''}"
    else:
        payload = json.dumps(
            {
                "file": normalized_file,
                "title": title,
                "project": project_name or "",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    digest = hashlib.blake2b(payload.encode("utf-8"), digest_size=6).hexdigest()
    return f"{prefix}_{digest}"


def _format_list_line(
    spec_file: str,
    title: str,
    project_name: str | None,
) -> str:
    if project_name:
        return f"[{project_name}] › {spec_file} › {title}"
    if title:
        return f"{spec_file} › {title}"
    return spec_file


def _explicit_test_id(raw_annotations: object) -> str | None:
    if not isinstance(raw_annotations, list):
        return None
    for entry in raw_annotations:
        if not isinstance(entry, dict):
            continue
        if entry.get("type") != "test-id":
            continue
        description = entry.get("description")
        if description:
            return str(description).strip()
    return None


def _walk_json_suites(
    node: dict[str, Any],
    titles: list[str],
    project_id: str,
    *,
    seen_ids: dict[str, DiscoveredTest],
    warnings: list[str],
) -> list[DiscoveredTest]:
    tests: list[DiscoveredTest] = []

    for spec in node.get("specs", []) or []:
        if not isinstance(spec, dict):
            warnings.append("skipped spec entry that was not a mapping")
            continue
        spec_title = str(spec.get("title", "")).strip()
        full_title = " › ".join([*titles, spec_title]) if titles else spec_title
        spec_file = str(spec.get("file") or node.get("file") or "").replace(
            "\\", "/"
        )
        line = spec.get("line")
        line_number = int(line) if isinstance(line, int) else None
        project_entries = spec.get("tests") or [{}]
        seen_projects: set[str] = set()
        for entry in project_entries:
            if not isinstance(entry, dict):
                warnings.append(f"skipped project entry in {spec_file!r}")
                continue
            project_name = str(entry.get("projectName") or "").strip() or None
            if project_name in seen_projects:
                continue
            seen_projects.add(project_name or "")
            explicit_id = _explicit_test_id(entry.get("annotations"))
            test_id = build_test_id(
                project_id,
                spec_file,
                full_title,
                project_name,
                explicit_test_id=explicit_id,
            )
            raw_list_line = _format_list_line(
                spec_file, full_title, project_name
            )
            discovered = DiscoveredTest(
                id=test_id,
                title=full_title,
                spec_file=spec_file,
                project_name=project_name,
                line=line_number,
                raw_list_line=raw_list_line,
            )
            if test_id in seen_ids:
                previous = seen_ids[test_id]
                raise CatalogError(
                    "duplicate discovered test id "
                    f"{test_id!r} for {raw_list_line!r} and "
                    f"{previous.raw_list_line!r}"
                )
            seen_ids[test_id] = discovered
            tests.append(discovered)

    for child in node.get("suites", []) or []:
        if not isinstance(child, dict):
            warnings.append("skipped suite entry that was not a mapping")
            continue
        child_title = str(child.get("title", ""))
        child_file = child.get("file")
        if child_file and child_title == str(child_file):
            tests.extend(
                _walk_json_suites(
                    child,
                    [],
                    project_id,
                    seen_ids=seen_ids,
                    warnings=warnings,
                )
            )
        else:
            tests.extend(
                _walk_json_suites(
                    child,
                    [*titles, child_title],
                    project_id,
                    seen_ids=seen_ids,
                    warnings=warnings,
                )
            )

    return tests


def _parse_json_inventory(output: str, project_id: str) -> TestInventory:
    text = output.strip()
    brace = text.find("{")
    if brace == -1:
        raise CatalogError("no JSON object found in Playwright list output")
    try:
        data = json.loads(text[brace:])
    except json.JSONDecodeError as exc:
        raise CatalogError(f"could not parse Playwright JSON: {exc}") from exc

    seen_ids: dict[str, DiscoveredTest] = {}
    warnings: list[str] = []
    root = {"suites": data.get("suites", [])}
    tests = _walk_json_suites(
        root,
        [],
        project_id,
        seen_ids=seen_ids,
        warnings=warnings,
    )
    return TestInventory(tests=tuple(tests), warnings=tuple(warnings))


def _normalize_text_file_path(raw_file: str) -> str:
    normalized = _LINE_COL_RE.sub(r"\1", raw_file.strip()).strip()
    return normalized.replace("\\", "/")


def _parse_text_inventory(output: str, project_id: str) -> TestInventory:
    tests: list[DiscoveredTest] = []
    warnings: list[str] = []
    seen_ids: dict[str, DiscoveredTest] = {}

    for line_number, raw_line in enumerate(output.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _LIST_LINE_RE.match(line)
        if match is None:
            warnings.append(
                f"line {line_number}: could not parse list entry: {raw_line!r}"
            )
            continue
        project_name = match.group("project")
        spec_file = _normalize_text_file_path(match.group("file") or "")
        title = (match.group("title") or "").strip()
        if not spec_file:
            warnings.append(
                f"line {line_number}: missing spec file in {raw_line!r}"
            )
            continue
        test_id = build_test_id(project_id, spec_file, title, project_name)
        discovered = DiscoveredTest(
            id=test_id,
            title=title,
            spec_file=spec_file,
            project_name=project_name,
            raw_list_line=line,
        )
        if test_id in seen_ids:
            previous = seen_ids[test_id]
            raise CatalogError(
                "duplicate discovered test id "
                f"{test_id!r} for {line!r} and {previous.raw_list_line!r}"
            )
        seen_ids[test_id] = discovered
        tests.append(discovered)

    return TestInventory(tests=tuple(tests), warnings=tuple(warnings))


def parse_playwright_list(output: str, project_id: str) -> TestInventory:
    """Parse Playwright list output into discovered tests."""

    stripped = output.lstrip()
    if stripped.startswith("{"):
        return _parse_json_inventory(output, project_id)
    return _parse_text_inventory(output, project_id)


def run_playwright_list(config: EffectiveConfig) -> str:
    """Run the configured Playwright list command and return stdout."""

    list_command = config.playwright.list_command
    if list_command is None:
        raise CatalogError("playwright.list_command is not configured")

    cmd = list(list_command.argv)
    if not any(
        arg.startswith("--reporter=") or arg == "--reporter" for arg in cmd
    ):
        cmd.extend(_JSON_REPORTER_ARGS)

    test_dir = config.project_root / config.playwright.cwd
    if not test_dir.is_dir():
        raise CatalogError(f"playwright cwd does not exist: {test_dir}")

    env = dict(list_command.env)
    run_env = None
    if env:
        run_env = {**os.environ, **env}
    try:
        result = subprocess.run(
            cmd,
            cwd=str(test_dir),
            env=run_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            check=False,
        )
    except FileNotFoundError as exc:
        raise CatalogError(
            f"could not launch Playwright list command ({cmd[0]!r} not found)"
        ) from exc

    stdout = result.stdout
    if result.returncode != 0 and "{" not in stdout:
        raise CatalogError(
            "playwright list command failed "
            f"(exit {result.returncode}):\n{result.stderr.strip()}"
        )
    return stdout
