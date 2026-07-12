"""Live agent plugin contract tests (opt-in via env var)."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from e2e_ai.agents.invocation import (
    EXIT_QUOTA_ERROR,
    classify_agent_exit,
)
from e2e_ai.agents.plugins.claude import create_claude_agent
from e2e_ai.agents.plugins.codex import create_codex_agent
from e2e_ai.agents.plugins.cursor import create_cursor_agent
from e2e_ai.agents.routing_outcomes import classify_invocation_exit
from e2e_ai.agents.schemas import PlanRequest
from e2e_ai.config import (
    AgentConfig,
    CommandSpec,
    EffectiveConfig,
    IsolationConfig,
    PlaywrightConfig,
    RepairPolicy,
    RoutingConfig,
)
from e2e_ai.mcp.models import PlaywrightMcpConfig

pytestmark = pytest.mark.skipif(
    not os.environ.get("E2E_AI_LIVE_AGENT_TESTS"),
    reason="set E2E_AI_LIVE_AGENT_TESTS=1 to run live agent CLI tests",
)

LIVE_PROMPT = "Say ok. Respond with nothing else."
LIVE_TIMEOUT_SECONDS = 180
REPO_ROOT = Path(__file__).resolve().parents[1]


def _routing() -> RoutingConfig:
    return RoutingConfig(allow_canary=False, planner_requires_schema=False)


def _effective_config(tmp_path: Path) -> EffectiveConfig:
    playwright = PlaywrightConfig(
        list_command=CommandSpec(argv=("echo", "list")),
        run_command=CommandSpec(argv=("echo", "run")),
    )
    return EffectiveConfig(
        project_id="live",
        project_root=tmp_path,
        state_dir=tmp_path / ".e2e-ai",
        playwright=playwright,
        agents=(),
        isolation=IsolationConfig(),
        exclude=(),
        repair_policy=RepairPolicy(),
        routing=_routing(),
        playwright_mcp=PlaywrightMcpConfig(),
    )


def _plan_request(tmp_path: Path) -> PlanRequest:
    return PlanRequest(
        prompt=LIVE_PROMPT,
        work_dir=REPO_ROOT,
        timeout_seconds=LIVE_TIMEOUT_SECONDS,
        log_dir=tmp_path / "agents",
        profile=None,
        require_schema=False,
    )


def _skip_unless_logged_in(plugin) -> None:
    health = plugin.check_login()
    if not health.logged_in:
        pytest.skip(
            f"{plugin.id} not logged in: {health.reason or 'auth check failed'}"
        )


def _skip_unless_executable(plugin) -> None:
    if (
        shutil.which(plugin.executable) is None
        and not Path(plugin.executable).is_file()
    ):
        pytest.skip(f"{plugin.id} executable not found: {plugin.executable}")


def _parse_json_lines(stdout: str) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def _assert_stream_json_shape(stdout: str) -> None:
    records = _parse_json_lines(stdout)
    assert records, "expected at least one JSON line in agent stdout"
    assert "Workspace Trust Required" not in stdout


def _assert_successful_live_result(plugin, result) -> None:
    assert result.exit_code == 0, result.stdout[-2000:]
    assert result.ok is True
    assert "Workspace Trust Required" not in result.stdout
    _assert_stream_json_shape(result.stdout)

    run = result.to_agent_run_result()
    exit_class = classify_invocation_exit(
        run,
        role="planner",
        config=_effective_config(Path("/tmp/live")),
        plan_text=result.stdout,
    )
    assert exit_class is None
    if result.exit_code != 0:
        assert (
            classify_agent_exit(result.exit_code, result.stdout, result.stderr)
            != EXIT_QUOTA_ERROR
        )


class TestLiveCodex:
    def test_plan_say_ok_via_production_plugin_path(
        self, tmp_path: Path
    ) -> None:
        plugin = create_codex_agent(
            AgentConfig(id="codex", enabled=True, executable="codex"),
            _routing(),
        )
        _skip_unless_executable(plugin)
        _skip_unless_logged_in(plugin)

        result = plugin.plan(_plan_request(tmp_path))

        if result.exit_code != 0:
            exit_class = classify_agent_exit(
                result.exit_code,
                result.stdout,
                result.stderr,
            )
            if exit_class == EXIT_QUOTA_ERROR:
                pytest.skip("codex quota exhausted")

        _assert_successful_live_result(plugin, result)
        records = _parse_json_lines(result.stdout)
        assert not any(record.get("type") == "error" for record in records)


class TestLiveClaude:
    def test_plan_say_ok_via_production_plugin_path(
        self, tmp_path: Path
    ) -> None:
        plugin = create_claude_agent(
            AgentConfig(id="claude", enabled=True, executable="claude"),
            _routing(),
        )
        _skip_unless_executable(plugin)
        _skip_unless_logged_in(plugin)

        result = plugin.plan(_plan_request(tmp_path))

        _assert_successful_live_result(plugin, result)
        records = _parse_json_lines(result.stdout)
        if any(record.get("type") == "rate_limit_event" for record in records):
            assert (
                classify_agent_exit(1, result.stdout, result.stderr)
                != EXIT_QUOTA_ERROR
            )
        assert not any(record.get("type") == "error" for record in records)


class TestLiveCursor:
    def test_plan_say_ok_via_production_plugin_path(
        self, tmp_path: Path
    ) -> None:
        executable = os.environ.get(
            "E2E_AI_CURSOR_EXECUTABLE",
            "C:/Users/Nicu/AppData/Local/cursor-agent/agent.cmd",
        )
        plugin = create_cursor_agent(
            AgentConfig(id="cursor", enabled=True, executable=executable),
            _routing(),
        )
        _skip_unless_executable(plugin)
        _skip_unless_logged_in(plugin)

        result = plugin.plan(_plan_request(tmp_path))

        _assert_successful_live_result(plugin, result)
        argv = plugin.build_plan_argv(
            _plan_request(tmp_path),
            schema=None,
        )
        assert "--trust" in argv
