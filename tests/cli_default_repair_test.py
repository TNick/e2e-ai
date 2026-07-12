"""Tests for the top-level ``e2e-ai`` repair shortcut."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from e2e_ai import cli as cli_mod


class TestDefaultRepair:
    """The root command should behave like ``repair``."""

    def test_root_help_mentions_default_repair(self) -> None:
        result = CliRunner().invoke(cli_mod.build_cli(), ["--help"])
        assert result.exit_code == 0, result.output
        assert "runs `repair` with the same options" in result.output

    def test_root_without_subcommand_invokes_repair(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        calls: list[dict[str, object]] = []

        def fake_run_repair(**kwargs) -> None:
            calls.append(kwargs)

        monkeypatch.setattr(cli_mod, "_run_repair", fake_run_repair)
        result = CliRunner().invoke(
            cli_mod.build_cli(),
            [
                "--project-root",
                str(tmp_path),
                "--limit",
                "5",
                "--test-id",
                "demo_123",
                "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        assert calls == [
            {
                "project_root": tmp_path,
                "limit": 5,
                "test_id": "demo_123",
                "max_attempts": None,
                "rediscover": True,
                "skip_login_check": False,
                "dry_run_agents": False,
                "dry_run": True,
                "start_runtime": True,
                "failed_only": False,
            }
        ]
