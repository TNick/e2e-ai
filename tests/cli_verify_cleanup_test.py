"""Tests for the ``e2e-ai verify`` clean gate and ``e2e-ai cleanup`` command."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import click
import pytest

from e2e_ai import cli as cli_mod
from e2e_ai.config import load_effective_config


def _report(
    tmp_path: Path,
    name: str,
    *,
    expected: int,
    unexpected: int,
    skipped: int = 0,
    flaky: int = 0,
) -> Path:
    path = tmp_path / name
    path.write_text(
        json.dumps(
            {
                "stats": {
                    "expected": expected,
                    "unexpected": unexpected,
                    "skipped": skipped,
                    "flaky": flaky,
                },
                "suites": [],
            }
        ),
        encoding="utf-8",
    )
    return path


class TestVerifyGate:
    def test_clean_report_passes(self, tmp_path):
        report = _report(tmp_path, "r.json", expected=5, unexpected=0)
        assert cli_mod._gate_reports([report], allow_skips=False) is True

    def test_unexpected_failure_fails(self, tmp_path):
        report = _report(tmp_path, "r.json", expected=4, unexpected=1)
        assert cli_mod._gate_reports([report], allow_skips=False) is False

    def test_flaky_fails(self, tmp_path):
        report = _report(tmp_path, "r.json", expected=4, unexpected=0, flaky=1)
        assert cli_mod._gate_reports([report], allow_skips=False) is False

    def test_skips_fail_by_default_but_allowed_with_flag(self, tmp_path):
        report = _report(
            tmp_path, "r.json", expected=4, unexpected=0, skipped=2
        )
        assert cli_mod._gate_reports([report], allow_skips=False) is False
        assert cli_mod._gate_reports([report], allow_skips=True) is True

    def test_sharded_reports_are_summed(self, tmp_path):
        # A directory of shard reports: one clean, one with a failure.
        shards = tmp_path / "shards"
        shards.mkdir()
        _report(shards, "shard-1.json", expected=3, unexpected=0)
        _report(shards, "shard-2.json", expected=2, unexpected=1)
        assert cli_mod._gate_reports([shards], allow_skips=False) is False

    def test_no_reports_raises(self, tmp_path):
        with pytest.raises(click.ClickException):
            cli_mod._gate_reports([tmp_path], allow_skips=False)


PROJECT_YAML = textwrap.dedent(
    """
    project: {id: demo}
    state: {dir: .e2e-ai}
    playwright:
      cwd: e2e
      list_command: [echo, list]
      run_command: [echo, run]
    exclude: {tests: []}
    isolation:
      backend: docker_compose_postgres_template
      postgres:
        db_prefix: demo_
    agents:
      planner: {plugin: claude}
      implementer: {plugin: codex}
    """
)


def _config(tmp_path: Path):
    (tmp_path / "e2e").mkdir()
    (tmp_path / "e2e-ai.yml").write_text(PROJECT_YAML, encoding="utf-8")
    return load_effective_config(tmp_path)


class TestCleanup:
    def test_dry_run_counts_manifests_without_dropping(
        self, tmp_path, monkeypatch
    ):
        config = _config(tmp_path)
        # Two kept-db manifests under the state dir.
        for i, db in enumerate(("demo_a", "demo_b")):
            d = config.state_dir / "work" / f"t{i}" / "att0"
            d.mkdir(parents=True)
            (d / "cleanup-manifest.json").write_text(
                json.dumps({"database_name": db}), encoding="utf-8"
            )

        dropped_calls: list[str] = []
        monkeypatch.setattr(
            cli_mod,
            "drop_database",
            lambda ctx, name: dropped_calls.append(name),
        )
        dropped, failed = cli_mod._cleanup_databases(config, dry_run=True)
        assert dropped == 2
        assert failed == []
        assert dropped_calls == []  # dry-run drops nothing
        # Manifests are preserved on dry-run.
        assert list(config.state_dir.rglob("cleanup-manifest.json"))

    def test_drops_databases_and_removes_manifests(self, tmp_path, monkeypatch):
        config = _config(tmp_path)
        d = config.state_dir / "work" / "t0" / "att0"
        d.mkdir(parents=True)
        (d / "cleanup-manifest.json").write_text(
            json.dumps({"database_name": "demo_x"}), encoding="utf-8"
        )
        dropped_calls: list[str] = []
        monkeypatch.setattr(
            cli_mod,
            "drop_database",
            lambda ctx, name: dropped_calls.append(name),
        )
        dropped, failed = cli_mod._cleanup_databases(config, dry_run=False)
        assert dropped == 1
        assert dropped_calls == ["demo_x"]
        assert not list(config.state_dir.rglob("cleanup-manifest.json"))

    def test_purge_artifacts_removes_work_and_runs(self, tmp_path):
        config = _config(tmp_path)
        (config.state_dir / "work" / "t0").mkdir(parents=True)
        (config.state_dir / "runs" / "t0").mkdir(parents=True)
        removed = cli_mod._purge_artifacts(config, dry_run=False)
        assert removed == 2
        assert not (config.state_dir / "work").exists()
        assert not (config.state_dir / "runs").exists()
