"""Tests for the Playwright execution primitive (runner package)."""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

from e2e_ai.config import load_effective_config
from e2e_ai.inventory.models import (
    DiscoveredTest,  # noqa: E402  (grouped import)
)
from e2e_ai.runner.artifacts import (
    write_command_manifest,
    write_environment_manifest,
)
from e2e_ai.runner.commands import build_playwright_test_command
from e2e_ai.runner.models import TestRunRequest as RunRequest
from e2e_ai.runner.playwright import build_playwright_env
from e2e_ai.runner.results import attempt_status_from_report
from e2e_ai.runner.subprocess import run_command_to_logs

PROJECT_YAML = textwrap.dedent(
    """
    project: {id: demo}
    state: {dir: .e2e-ai}
    playwright:
      cwd: e2e
      list_command: [pnpm, exec, playwright, test, --list]
      run_command: [pnpm, exec, playwright, test]
    exclude: {tests: []}
    agents:
      planner: {plugin: claude}
      implementer: {plugin: codex}
    """
)


def _config(tmp_path: Path):
    (tmp_path / "e2e").mkdir()
    (tmp_path / "e2e-ai.yml").write_text(PROJECT_YAML, encoding="utf-8")
    return load_effective_config(tmp_path)


def _test() -> DiscoveredTest:
    return DiscoveredTest(
        id="demo_x",
        title="admin › does a thing",
        spec_file="a.spec.ts",
        project_name="chromium",
        line=12,
    )


class TestCommandBuilder:
    def test_builds_exact_title_command(self, tmp_path):
        config = _config(tmp_path)
        argv = build_playwright_test_command(config, _test())
        # run_command is prefixed verbatim.
        assert argv[:4] == ["pnpm", "exec", "playwright", "test"]
        # spec followed by -g and the leaf title as a single argv item.
        assert "a.spec.ts" in argv
        gi = argv.index("-g")
        assert argv[gi + 1] == "does a thing"
        assert (
            "--project" in argv
            and argv[argv.index("--project") + 1] == "chromium"
        )


class TestPlaywrightEnv:
    def test_sets_unique_report_paths(self, tmp_path):
        config = _config(tmp_path)
        request = RunRequest(
            run_id="r",
            test_id="demo_x",
            spec_file="a.spec.ts",
            title="t",
            attempt_index=0,
            work_dir=tmp_path,
        )
        json_a = tmp_path / "a" / "playwright-results.json"
        json_b = tmp_path / "b" / "playwright-results.json"
        env_a = build_playwright_env(
            config, request, json_a, tmp_path / "a.zip"
        )
        env_b = build_playwright_env(
            config, request, json_b, tmp_path / "b.zip"
        )
        json_env = config.playwright.report_env.json
        blob_env = config.playwright.report_env.blob
        assert env_a[json_env] == str(json_a)
        assert env_b[json_env] == str(json_b)
        assert env_a[json_env] != env_b[json_env]
        assert env_a[blob_env] != env_b[blob_env]


class TestSubprocess:
    def test_run_command_to_logs_captures_output(self, tmp_path):
        log = tmp_path / "output.log"
        code = run_command_to_logs(
            [
                sys.executable,
                "-c",
                (
                    "import sys; print('hello out'); "
                    "print('hello err', file=sys.stderr)"
                ),
            ],
            cwd=tmp_path,
            env={"PYTHONIOENCODING": "utf-8"},
            stdout_path=log,
            stderr_path=log,  # combined
            timeout_seconds=30,
        )
        assert code == 0
        text = log.read_text(encoding="utf-8")
        assert "hello out" in text
        assert "hello err" in text


class TestResults:
    def test_attempt_status_passed(self):
        data = {"stats": {"expected": 1, "unexpected": 0}}
        assert attempt_status_from_report(0, data) == "passed"

    def test_attempt_status_failed(self):
        data = {"stats": {"expected": 0, "unexpected": 1}}
        assert attempt_status_from_report(1, data) == "failed"


class TestArtifacts:
    def test_command_manifest_redacts_secret_keys(self, tmp_path):
        env = {"PATH": "/usr/bin", "API_TOKEN": "super-secret-value"}
        manifest = write_command_manifest(
            tmp_path, ["pnpm", "test"], tmp_path, list(env.keys())
        )
        text = manifest.read_text(encoding="utf-8")
        # command.json records key names only, never secret values.
        assert "API_TOKEN" in text
        assert "super-secret-value" not in text

        env_manifest = write_environment_manifest(tmp_path, env)
        data = json.loads(env_manifest.read_text(encoding="utf-8"))
        assert data["API_TOKEN"] == "***redacted***"
        assert data["PATH"] == "/usr/bin"
