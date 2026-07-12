"""Tests for agent plugins, routing, and invocation helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path

from e2e_ai.agents.capabilities import QUOTA_EXHAUSTED, QUOTA_READY, QUOTA_UNKNOWN
from e2e_ai.agents.invocation import (
    EXIT_AUTH_ERROR,
    EXIT_QUOTA_ERROR,
    classify_agent_exit,
    run_agent_command,
)
from e2e_ai.agents.plugins.claude import build_plan_mode_argv
from e2e_ai.agents.plugins.codex import build_login_argv
from e2e_ai.agents.plugins.cursor import create_cursor_agent
from e2e_ai.agents.quota import QuotaSnapshot, enough_quota
from e2e_ai.agents.registry import create_agent_plugins
from e2e_ai.agents.router import ROLE_TASK_CLASS, select_agent
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


def _config(
    *,
    planner: str = "codex",
    implementer: str = "claude",
    instrumenter: str = "claude",
) -> EffectiveConfig:
    playwright = PlaywrightConfig(
        list_command=CommandSpec(argv=("echo", "list")),
        run_command=CommandSpec(argv=("echo", "run")),
    )
    return EffectiveConfig(
        project_id="demo",
        project_root=Path("/tmp/demo"),
        state_dir=Path("/tmp/demo/.e2e-ai"),
        playwright=playwright,
        agents=(
            AgentConfig(id="planner", plugin=planner, profile="difficult"),
            AgentConfig(id="implementer", plugin=implementer, profile="cheap"),
            AgentConfig(id="instrumenter", plugin=instrumenter, profile="difficult"),
            AgentConfig(id="codex", enabled=True, executable="codex"),
            AgentConfig(id="claude", enabled=True, executable="claude"),
            AgentConfig(id="cursor", enabled=True, executable="agent"),
        ),
        isolation=IsolationConfig(),
        exclude=(),
        repair_policy=RepairPolicy(),
        routing=RoutingConfig(allow_canary=False),
        playwright_mcp=PlaywrightMcpConfig(),
    )


class TestQuota:
    def test_enough_quota_respects_task_class(self) -> None:
        ready = QuotaSnapshot(plugin_id="codex", state=QUOTA_READY)
        assert enough_quota("difficult", ready) is True
        exhausted = QuotaSnapshot(
            plugin_id="codex",
            state=QUOTA_EXHAUSTED,
            task_classes={"difficult": QUOTA_EXHAUSTED},
        )
        assert enough_quota("difficult", exhausted) is False
        unknown = QuotaSnapshot(
            plugin_id="cursor",
            state=QUOTA_UNKNOWN,
            optimistic=True,
        )
        assert enough_quota("normal", unknown) is True
        pessimistic = QuotaSnapshot(
            plugin_id="cursor",
            state=QUOTA_UNKNOWN,
            optimistic=False,
        )
        assert enough_quota("normal", pessimistic) is False


class TestAgentExit:
    def test_classifies_auth_error(self) -> None:
        assert (
            classify_agent_exit(1, "", "Error: not logged in to Claude")
            == EXIT_AUTH_ERROR
        )

    def test_classifies_quota_error(self) -> None:
        assert classify_agent_exit(1, "rate limit exceeded", "") == EXIT_QUOTA_ERROR


class TestAgentInvocation:
    def test_argument_transport_does_not_inherit_stdin(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        seen: dict[str, object] = {}

        class FakeProcess:
            returncode = 0

            def communicate(self, input=None, timeout=None):
                seen["input"] = input
                seen["timeout"] = timeout
                return None

        def fake_popen(argv, **kwargs):
            seen["argv"] = argv
            seen["stdin"] = kwargs["stdin"]
            return FakeProcess()

        monkeypatch.setattr("e2e_ai.agents.invocation.subprocess.Popen", fake_popen)

        exit_code = run_agent_command(
            ["agent", "prompt as arg"],
            cwd=tmp_path,
            env={},
            stdin_data=None,
            stdout_path=tmp_path / "agent.log",
            stderr_path=tmp_path / "agent.log",
            timeout_seconds=10,
        )

        assert exit_code == 0
        assert seen["stdin"] == subprocess.DEVNULL
        assert seen["input"] is None


class TestRouter:
    def test_selects_planner_and_implementer_roles(self, monkeypatch) -> None:
        config = _config(planner="codex", implementer="claude")
        plugins = create_agent_plugins(config)

        def always_ready(plugin, **_kwargs):
            return 100

        monkeypatch.setattr(
            "e2e_ai.agents.router._score_candidate",
            always_ready,
        )
        planner = select_agent(
            config,
            "planner",
            ROLE_TASK_CLASS["planner"],
            plugins,
        )
        implementer = select_agent(
            config,
            "implementer",
            ROLE_TASK_CLASS["implementer"],
            plugins,
        )
        assert planner.id == "codex"
        assert implementer.id == "claude"


class TestCodex:
    def test_builds_login_command(self) -> None:
        argv = build_login_argv("codex")
        assert argv == ["codex", "login", "status"]

    def test_uses_stdin_prompt_transport(self) -> None:
        agent = create_agent_plugins(_config())["codex"]
        assert agent.prompt_transport == "stdin"

    def test_build_exec_argv_uses_schema_file_path(self, tmp_path: Path) -> None:
        from e2e_ai.agents.plugins.codex import build_exec_argv

        schema_path = tmp_path / "plan-schema.json"
        schema_path.write_text('{"type":"object"}', encoding="utf-8")
        argv = build_exec_argv(
            "codex",
            sandbox="read-only",
            schema_path=schema_path,
        )
        idx = argv.index("--output-schema")
        assert argv[idx + 1] == str(schema_path)
        assert "{" not in argv[idx + 1]


class TestClaude:
    def test_builds_plan_mode_command(self) -> None:
        argv = build_plan_mode_argv("claude", schema={"type": "object"})
        assert argv[:4] == ["claude", "-p", "--permission-mode", "plan"]
        assert "--output-format" in argv
        assert "--json-schema" in argv


class TestCursor:
    def test_quota_unknown_without_canary(self) -> None:
        agent = create_cursor_agent(
            AgentConfig(id="cursor", enabled=True),
            RoutingConfig(allow_canary=False),
        )
        snapshot = agent.quota("normal")
        assert snapshot.state == QUOTA_UNKNOWN
        assert snapshot.optimistic is False


class TestMcpCapabilities:
    def test_codex_plugin_supports_mcp(self) -> None:
        agent = create_agent_plugins(_config())["codex"]
        caps = agent.discover()
        assert caps.supports_mcp is True
        assert agent.supports_playwright_mcp() is True
