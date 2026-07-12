"""Tests for agent plugins, routing, and invocation helpers."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from e2e_ai.agents.base import AgentRunResult
from e2e_ai.agents.capabilities import (
    QUOTA_EXHAUSTED,
    QUOTA_READY,
    QUOTA_UNKNOWN,
)
from e2e_ai.agents.invocation import (
    EXIT_AUTH_ERROR,
    EXIT_MAX_TURNS_EXCEEDED,
    EXIT_PERMISSION_DENIED,
    EXIT_QUOTA_ERROR,
    EXIT_TASK_FAILURE,
    classify_agent_exit,
    run_agent_command,
)
from e2e_ai.agents.plugins._common import remove_temporary_path
from e2e_ai.agents.plugins.claude import (
    DEFAULT_INSTRUMENTER_MAX_TURNS,
    DEFAULT_PLANNER_MAX_TURNS,
    build_plan_mode_argv,
    create_claude_agent,
)
from e2e_ai.agents.plugins.codex import (
    build_exec_argv,
    build_login_argv,
    prepare_codex_mcp_runtime,
)
from e2e_ai.agents.plugins.cursor import (
    build_implement_argv_list,
    build_plan_argv_list,
    create_cursor_agent,
)
from e2e_ai.agents.quota import QuotaSnapshot, enough_quota
from e2e_ai.agents.registry import create_agent_plugins
from e2e_ai.agents.router import ROLE_TASK_CLASS, select_agent, select_provider
from e2e_ai.agents.routing_outcomes import (
    RoutingAction,
    classify_invocation_exit,
    decide_routing_action,
)
from e2e_ai.agents.schemas import (
    ImplementRequest,
    InstrumentRequest,
    PlanRequest,
)
from e2e_ai.config import (
    AgentConfig,
    CommandSpec,
    EffectiveConfig,
    IsolationConfig,
    PlaywrightConfig,
    RepairPolicy,
    RolePreferencesConfig,
    RoutingConfig,
)
from e2e_ai.mcp.models import PlaywrightMcpConfig

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "agents"


def _plan_request(tmp_path: Path | None = None) -> PlanRequest:
    work_dir = tmp_path or Path("/tmp/demo")
    return PlanRequest(
        prompt="plan",
        work_dir=work_dir,
        profile=None,
        require_schema=True,
    )


def _instrument_request(tmp_path: Path | None = None) -> InstrumentRequest:
    work_dir = tmp_path or Path("/tmp/demo")
    return InstrumentRequest(
        prompt="instrument",
        work_dir=work_dir,
        profile=None,
        require_schema=True,
    )


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
            AgentConfig(
                id="instrumenter", plugin=instrumenter, profile="difficult"
            ),
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
        assert (
            classify_agent_exit(1, "rate limit exceeded", "")
            == EXIT_QUOTA_ERROR
        )

    def test_classifies_codex_usage_limit_jsonl(self) -> None:
        stdout = (
            '{"type":"error","message":"You\'ve hit your usage limit."}\n'
            '{"type":"turn.failed","error":{"message":"usage limit"}}\n'
        )
        assert classify_agent_exit(2, stdout, "") == EXIT_QUOTA_ERROR

    def test_ignores_claude_rate_limit_event_metadata(self) -> None:
        stdout = (
            '{"type":"rate_limit_event","rate_limit_info":{'
            '"status":"allowed","rateLimitType":"five_hour"}}\n'
        )
        assert classify_agent_exit(1, stdout, "") == EXIT_TASK_FAILURE

    def test_classifies_claude_error_max_turns(self) -> None:
        stdout = (
            '{"type":"result","subtype":"error_max_turns",'
            '"is_error":true,"num_turns":7}\n'
        )
        assert classify_agent_exit(1, stdout, "") == EXIT_MAX_TURNS_EXCEEDED

    def test_classifies_cursor_workspace_trust_prompt(self) -> None:
        stdout = (
            "Workspace Trust Required\n"
            "Pass --trust, --yolo, or -f if you trust this directory\n"
        )
        assert classify_agent_exit(1, stdout, "") == EXIT_PERMISSION_DENIED

    def test_real_claude_fixture_classifies_max_turns_not_quota(
        self,
    ) -> None:
        stdout = (FIXTURES / "claude-max-turns-rate-limit-event.log").read_text(
            encoding="utf-8"
        )
        assert classify_agent_exit(1, stdout, "") == EXIT_MAX_TURNS_EXCEEDED


class TestInvocationExitRouting:
    def test_reclassifies_task_failure_from_stdout(self) -> None:
        stdout = '{"type":"error","message":"You\'ve hit your usage limit."}\n'
        run = AgentRunResult(
            "codex",
            2,
            stdout,
            "",
            exit_class="task_failure",
        )
        exit_class = classify_invocation_exit(
            run,
            role="instrumenter",
            config=_config(),
            plan_text=stdout,
        )
        assert exit_class == EXIT_QUOTA_ERROR

    def test_success_ignores_quota_text_in_stdout(self) -> None:
        stdout = (
            '{"type":"error","message":"You\'ve hit your usage limit."}\n'
            '{"type":"turn.completed"}\n'
        )
        run = AgentRunResult("codex", 0, stdout, "", exit_class="quota_error")
        exit_class = classify_invocation_exit(
            run,
            role="instrumenter",
            config=_config(),
            plan_text=stdout,
        )
        assert exit_class is None
        assert (
            classify_agent_exit(0, "rate limit exceeded", "")
            == EXIT_TASK_FAILURE
        )

    def test_max_turns_exceeded_switches_provider(self) -> None:
        action = decide_routing_action(
            EXIT_MAX_TURNS_EXCEEDED,
            config=_config(),
            providers_remaining=True,
            switches_remaining=True,
        )
        assert action is RoutingAction.SWITCH_PROVIDER

    def test_permission_denied_switches_provider(self) -> None:
        action = decide_routing_action(
            EXIT_PERMISSION_DENIED,
            config=_config(),
            providers_remaining=True,
            switches_remaining=True,
        )
        assert action is RoutingAction.SWITCH_PROVIDER


class TestAgentInvocation:
    def test_removes_temporary_directory(self, tmp_path: Path) -> None:
        runtime_home = tmp_path / "codex-home-agent"
        runtime_home.mkdir()
        (runtime_home / "profile.config.toml").write_text(
            "[mcp_servers.playwright]\n",
            encoding="utf-8",
        )

        remove_temporary_path(runtime_home)

        assert not runtime_home.exists()

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

        monkeypatch.setattr(
            "e2e_ai.agents.invocation.subprocess.Popen", fake_popen
        )

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

    def test_build_exec_argv_uses_schema_file_path(
        self, tmp_path: Path
    ) -> None:
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

    def test_build_exec_argv_sets_approval_via_config_override(self) -> None:
        argv = build_exec_argv(
            "codex",
            sandbox="workspace-write",
            approval="never",
        )
        assert "--ask-for-approval" not in argv
        assert "--ignore-user-config" in argv
        assert argv[argv.index("-c") + 1] == "approval_policy=never"
        if os.name == "nt":
            assert argv[argv.index("--sandbox") + 1] == "danger-full-access"

    def test_build_exec_argv_layers_mcp_profile(self) -> None:
        argv = build_exec_argv(
            "codex",
            sandbox="workspace-write",
            mcp_profile="e2e-ai-mcp-test",
        )
        assert argv.count("-p") == 1
        assert argv[argv.index("-p") + 1] == "e2e-ai-mcp-test"

    def test_prepare_codex_mcp_runtime_copies_profile(
        self, tmp_path: Path
    ) -> None:
        from e2e_ai.mcp.models import AgentMcpAttachment, McpSessionSpec

        client_dir = tmp_path / "client"
        client_dir.mkdir()
        client_path = client_dir / "codex-mcp.toml"
        client_path.write_text(
            "[mcp_servers.playwright]\nrequired = true\n", encoding="utf-8"
        )
        session = McpSessionSpec(
            session_id="agent_test",
            test_id="t1",
            variant_key="v1",
            attempt_id="a1",
            role="instrumenter",
            output_dir=tmp_path / "output",
            config_path=tmp_path / "playwright-mcp.json",
            allowed_origins=("http://localhost/",),
        )
        attachment = AgentMcpAttachment(
            enabled=True,
            session=session,
            client_config_path=client_path,
        )
        log_dir = tmp_path / "agents"
        env, profile, cleanup = prepare_codex_mcp_runtime(
            attachment,
            log_dir=log_dir,
            env={"PATH": "/usr/bin"},
        )
        assert profile is not None
        assert profile.startswith("e2e-ai-mcp-")
        assert "CODEX_HOME" in env
        profile_path = Path(env["CODEX_HOME"]) / f"{profile}.config.toml"
        assert profile_path.is_file()
        assert cleanup


class TestClaude:
    def test_builds_plan_mode_command(self) -> None:
        argv = build_plan_mode_argv("claude", schema={"type": "object"})
        assert argv[:4] == ["claude", "-p", "--permission-mode", "plan"]
        assert "--output-format" in argv
        assert "--json-schema" in argv
        assert "--verbose" in argv

    def test_planner_defaults_to_higher_max_turns(self) -> None:
        agent = create_claude_agent(
            AgentConfig(id="claude", enabled=True),
            RoutingConfig(allow_canary=False),
        )
        argv = agent.build_plan_argv(
            _plan_request(),
            schema={"type": "object"},
        )
        assert argv[argv.index("--max-turns") + 1] == str(
            DEFAULT_PLANNER_MAX_TURNS
        )

    def test_instrumenter_defaults_to_higher_max_turns(self) -> None:
        agent = create_claude_agent(
            AgentConfig(id="claude", enabled=True),
            RoutingConfig(allow_canary=False),
        )
        argv = agent.build_instrument_argv(
            _instrument_request(),
            schema={"type": "object"},
        )
        assert argv[argv.index("--max-turns") + 1] == str(
            DEFAULT_INSTRUMENTER_MAX_TURNS
        )

    def test_max_turns_override_from_config(self) -> None:
        agent = create_claude_agent(
            AgentConfig(id="claude", enabled=True, max_turns=9),
            RoutingConfig(allow_canary=False),
        )
        argv = agent.build_plan_argv(
            _plan_request(),
            schema={"type": "object"},
        )
        assert argv[argv.index("--max-turns") + 1] == "9"


class TestCursor:
    def test_uses_stdin_prompt_transport(self) -> None:
        agent = create_cursor_agent(
            AgentConfig(id="cursor", enabled=True),
            RoutingConfig(allow_canary=False),
        )

        assert agent.prompt_transport == "stdin"

    def test_auto_variant_omits_model_flag(self) -> None:
        argv = build_plan_argv_list("agent")
        assert "--model" not in argv
        assert "--trust" in argv

    def test_plan_argv_includes_trust(self) -> None:
        argv = build_plan_argv_list("agent")
        assert "--trust" in argv

    def test_implement_argv_includes_trust_and_force(self) -> None:
        argv = build_implement_argv_list("agent", force=True)
        assert "--trust" in argv
        assert "--force" in argv

    def test_implement_plugin_always_forces(self) -> None:
        agent = create_cursor_agent(
            AgentConfig(id="cursor", enabled=True),
            RoutingConfig(allow_canary=False),
        )
        argv = agent.build_implement_argv(
            ImplementRequest(prompt="implement", work_dir=Path("/tmp/demo")),
        )
        assert "--trust" in argv
        assert "--force" in argv

    def test_gpt_variant_includes_model_flag(self) -> None:
        argv = build_plan_argv_list("agent", model="gpt-5.6-sol")
        assert argv[argv.index("--model") + 1] == "gpt-5.6-sol"

    def test_composer_implementer_includes_model_flag(self) -> None:
        argv = build_implement_argv_list(
            "agent",
            force=True,
            model="composer-2.5",
        )
        assert argv[argv.index("--model") + 1] == "composer-2.5"

    def test_quota_unknown_without_canary(self) -> None:
        agent = create_cursor_agent(
            AgentConfig(id="cursor", enabled=True),
            RoutingConfig(allow_canary=False),
        )
        snapshot = agent.quota("normal")
        assert snapshot.state == QUOTA_UNKNOWN
        assert snapshot.optimistic is False


class TestProviderVariants:
    def test_resolved_model_is_none_without_candidates(self) -> None:
        agent = create_cursor_agent(
            AgentConfig(id="cursor", enabled=True),
            RoutingConfig(allow_canary=False),
        )
        assert agent.resolved_model is None

    def test_create_variant_plugins(self) -> None:
        config = EffectiveConfig(
            project_id="demo",
            project_root=Path("/tmp/demo"),
            state_dir=Path("/tmp/demo/.e2e-ai"),
            playwright=PlaywrightConfig(
                list_command=CommandSpec(argv=("echo", "list")),
                run_command=CommandSpec(argv=("echo", "run")),
            ),
            agents=(
                AgentConfig(
                    id="cursor_gpt",
                    provider="cursor",
                    model_candidates=("gpt-5.6-sol",),
                ),
                AgentConfig(id="cursor", enabled=True, executable="agent"),
            ),
            isolation=IsolationConfig(),
            exclude=(),
            repair_policy=RepairPolicy(),
            routing=RoutingConfig(
                role_preferences=RolePreferencesConfig(
                    planner=("cursor_gpt",),
                ),
            ),
        )
        plugins = create_agent_plugins(config)
        assert "cursor_gpt" in plugins
        assert plugins["cursor_gpt"].id == "cursor_gpt"

    def test_select_provider_skips_unavailable_variant(
        self, monkeypatch
    ) -> None:
        config = EffectiveConfig(
            project_id="demo",
            project_root=Path("/tmp/demo"),
            state_dir=Path("/tmp/demo/.e2e-ai"),
            playwright=PlaywrightConfig(
                list_command=CommandSpec(argv=("echo", "list")),
                run_command=CommandSpec(argv=("echo", "run")),
            ),
            agents=(
                AgentConfig(id="cursor_gpt", provider="cursor"),
                AgentConfig(id="cursor", enabled=True, executable="agent"),
                AgentConfig(id="codex", enabled=True, executable="codex"),
            ),
            isolation=IsolationConfig(),
            exclude=(),
            repair_policy=RepairPolicy(),
            routing=RoutingConfig(
                role_preferences=RolePreferencesConfig(
                    planner=("cursor_gpt", "codex"),
                ),
            ),
        )
        plugins = create_agent_plugins(config)

        class UnavailableVariant:
            id = "cursor_gpt"

            def check_login(self):
                from e2e_ai.agents.capabilities import QUOTA_READY, AgentHealth

                return AgentHealth(
                    agent_id=self.id,
                    logged_in=True,
                    verified=True,
                    state=QUOTA_READY,
                )

            def discover(self):
                from e2e_ai.agents.capabilities import AgentCapabilities

                return AgentCapabilities(plugin_id=self.id, schema_mode=True)

            def quota(self, task_class):
                from e2e_ai.agents.capabilities import QUOTA_READY
                from e2e_ai.agents.quota import QuotaSnapshot

                _ = task_class
                return QuotaSnapshot(plugin_id=self.id, state=QUOTA_READY)

            def model_available(self):
                return False

        plugins["cursor_gpt"] = UnavailableVariant()  # type: ignore[assignment]

        selection = select_provider(
            config,
            "planner",
            ROLE_TASK_CLASS["planner"],
            plugins,
        )
        assert selection.selected_provider == "codex"


class TestMcpCapabilities:
    def test_codex_plugin_supports_mcp(self) -> None:
        agent = create_agent_plugins(_config())["codex"]
        caps = agent.discover()
        assert caps.supports_mcp is True
        assert agent.supports_playwright_mcp() is True
