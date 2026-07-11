"""Collect attempt artifacts and write command/environment manifests."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path

# Known artifact file names produced in an attempt work directory.
_ARTIFACT_NAMES = (
    "output.log",
    "stdout.log",
    "stderr.log",
    "playwright-results.json",
    "blob-report.zip",
    "command.json",
    "environment.json",
)

# Environment keys whose *values* must never be written to disk.
_SECRET_KEY_RE = re.compile(
    r"(token|secret|password|passwd|credential|api[_-]?key|auth|cookie|session)",
    re.IGNORECASE,
)


def collect_playwright_artifacts(work_dir: Path) -> list[Path]:
    """Return known artifacts for an attempt (existing files only)."""

    found: list[Path] = []
    for name in _ARTIFACT_NAMES:
        candidate = work_dir / name
        if candidate.is_file():
            found.append(candidate)
    artifacts_dir = work_dir / "artifacts"
    if artifacts_dir.is_dir():
        found.extend(sorted(p for p in artifacts_dir.rglob("*") if p.is_file()))
    return found


def _is_secret(key: str) -> bool:
    return bool(_SECRET_KEY_RE.search(key))


def write_command_manifest(
    work_dir: Path,
    argv: Sequence[str],
    cwd: Path,
    env_keys: Sequence[str],
) -> Path:
    """Write command metadata without leaking secret values.

    Only the environment *key names* are recorded here, never their values.
    """

    work_dir.mkdir(parents=True, exist_ok=True)
    path = work_dir / "command.json"
    payload = {
        "argv": list(argv),
        "cwd": str(cwd),
        "env_keys": sorted(env_keys),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def write_environment_manifest(
    work_dir: Path,
    env: Mapping[str, str],
) -> Path:
    """Write attempt-relevant environment, redacting secret-like values."""

    work_dir.mkdir(parents=True, exist_ok=True)
    path = work_dir / "environment.json"
    redacted = {
        key: ("***redacted***" if _is_secret(key) else str(value))
        for key, value in sorted(env.items())
    }
    path.write_text(json.dumps(redacted, indent=2), encoding="utf-8")
    return path
