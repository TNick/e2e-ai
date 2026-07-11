"""Tests for release-tag validation before PyPI publishing."""

from __future__ import annotations

from pathlib import Path

import pytest

from e2e_ai.release import check_release_version, load_project_version, main


class TestReleaseVersion:
    """Release version checks."""

    def test_loads_project_version(self, tmp_path: Path) -> None:
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            """
[project]
version = "1.2.3"
""",
            encoding="utf-8",
        )
        assert load_project_version(pyproject) == "1.2.3"

    def test_accepts_matching_tag(self, tmp_path: Path) -> None:
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            """
[project]
version = "1.2.3"
""",
            encoding="utf-8",
        )
        assert check_release_version("v1.2.3", pyproject) == "1.2.3"

    def test_rejects_mismatched_tag(self, tmp_path: Path) -> None:
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            """
[project]
version = "1.2.3"
""",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="does not match"):
            check_release_version("v1.2.4", pyproject)

    def test_main_returns_failure_for_mismatch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            """
[project]
version = "1.2.3"
""",
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "e2e_ai.release.default_pyproject_path",
            lambda: pyproject,
        )
        assert main(["v1.2.4"]) == 1

    def test_main_requires_one_argument(self) -> None:
        assert main([]) == 2
