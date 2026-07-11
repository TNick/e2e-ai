"""Tests for ``e2e-ai init`` scaffolding."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from e2e_ai.cli import build_cli

cli = build_cli()


class TestCliInit:
    """Init command writes detection-based target config."""

    def test_init_writes_target_section(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "package.json").write_text("{}", encoding="utf-8")
        result = CliRunner().invoke(cli, ["init"])
        assert result.exit_code == 0, result.output
        text = (tmp_path / "e2e-ai.yml").read_text(encoding="utf-8")
        assert "target:" in text
        assert "scope: frontend_only" in text

    def test_init_target_scope_flag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "package.json").write_text("{}", encoding="utf-8")
        (tmp_path / "backend").mkdir()
        result = CliRunner().invoke(
            cli,
            [
                "init",
                "--target-scope",
                "full-stack",
                "--backend-path",
                "backend",
            ],
        )
        assert result.exit_code == 0, result.output
        text = (tmp_path / "e2e-ai.yml").read_text(encoding="utf-8")
        assert "scope: full_stack" in text
        assert "backend:" in text

    def test_init_backend_reference_flag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "package.json").write_text("{}", encoding="utf-8")
        (tmp_path / "api").mkdir()
        result = CliRunner().invoke(
            cli,
            [
                "init",
                "--backend-reference",
                "--backend-path",
                "api",
            ],
        )
        assert result.exit_code == 0, result.output
        text = (tmp_path / "e2e-ai.yml").read_text(encoding="utf-8")
        assert "scope: frontend_with_backend_reference" in text
        assert "editable: false" in text
