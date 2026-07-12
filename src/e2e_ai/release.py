"""Helpers for validating release tags before publishing to PyPI."""

from __future__ import annotations

import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path


def default_pyproject_path() -> Path:
    """Return the repository ``pyproject.toml`` path."""

    return Path(__file__).resolve().parents[2] / "pyproject.toml"


def load_project_version(pyproject_path: Path | None = None) -> str:
    """Load the package version from ``pyproject.toml``."""

    path = pyproject_path or default_pyproject_path()
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    project = data.get("project")
    if not isinstance(project, dict):
        raise ValueError(f"{path} is missing a [project] table")
    version = project.get("version")
    if not isinstance(version, str) or not version.strip():
        raise ValueError(f"{path} is missing a [project].version value")
    return version.strip()


def check_release_version(
    tag_name: str,
    pyproject_path: Path | None = None,
) -> str:
    """Validate that a Git tag matches the project version."""

    normalized = tag_name.strip()
    if not normalized.startswith("v"):
        raise ValueError(f"release tag {tag_name!r} must start with 'v'")

    version = load_project_version(pyproject_path)
    tag_version = normalized.removeprefix("v")
    if tag_version != version:
        raise ValueError(
            f"release tag {normalized} does not match "
            f"pyproject.toml version {version}"
        )
    return version


def main(argv: Sequence[str] | None = None) -> int:
    """Validate a release tag and exit with a shell status code."""

    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: check_release_version.py <tag>", file=sys.stderr)
        return 2

    try:
        check_release_version(args[0])
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0
