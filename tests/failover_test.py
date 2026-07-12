"""Tests for provider failover and multi-agent routing."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import cast

from attrs import evolve

from e2e_ai.agents.base import AgentPlugin
from e2e_ai.agents.invocation import EXIT_AUTH_ERROR, EXIT_QUOTA_ERROR
from e2e_ai.agents.registry import AgentRegistry, create_agent_plugins
from e2e_ai.agents.router import _score_candidate, provider_pool, select_provider
from e2e_ai.agents.routing_outcomes import (
    EXIT_EMPTY_OUTPUT,
    RoutingAction,
    decide_routing_action,
)
from e2e_ai.config import (
    AgentConfig,
    CommandSpec,
    EffectiveConfig,
    FailoverConfig,
    IsolationConfig,
    PlaywrightConfig,
    RepairPolicy,
    RolePreferencesConfig,
    RoutingConfig,
)
from e2e_ai.db import ensure_database
from e2e_ai.mcp.models import PlaywrightMcpConfig
from e2e_ai.orchestrator.loop import (
    FailoverTracker,
    _invoke_role_with_failover,
)
from e2e_ai.orchestrator.store import (
    create_repair_run,
    record_agent_invocation,
)


def _seed_project(conn: sqlite3.Connection, project_id: str = "demo") -> None:
    conn.execute(
        """
        INSERT INTO projects (
            id, root_path, config_hash, created_at, updated_at
        )
        VALUES (?, '.', 'test', datetime('now'), datetime('now'))
        """,
        (project_id,),
    )
    conn.commit()


def _config(
    *,
    planner: tuple[str, ...] = ("codex", "claude"),
    implementer: tuple[str, ...] = ("claude", "codex"),
    instrumenter: tuple[str, ...] = ("cursor", "claude"),
    failover_enabled: bool = True,
    max_switches: int = 4,
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
            AgentConfig(id="planner", plugin=planner[0], profile="difficult"),
            AgentConfig(
                id="implementer",
                plugin=implementer[0],
                profile="cheap",
            ),
            AgentConfig(
                id="instrumenter",
                plugin=instrumenter[0],
                profile="difficult",
            ),
            AgentConfig(id="codex", enabled=True, executable="codex"),
            AgentConfig(id="claude", enabled=True, executable="claude"),
            AgentConfig(id="cursor", enabled=True, executable="agent"),
        ),
        isolation=IsolationConfig(),
        exclude=(),
        repair_policy=RepairPolicy(),
        routing=RoutingConfig(
            allow_canary=False,
            role_preferences=RolePreferencesConfig(
                planner=planner,
                implementer=implementer,
                instrumenter=instrumenter,
            ),
            failover=FailoverConfig(
                enabled=failover_enabled,
                max_switches_per_test=max_switches,
            ),
        ),
        playwright_mcp=PlaywrightMcpConfig(),
    )


class _ScriptedAgent:
    def __init__(
        self,
        agent_id: str,
        outcomes: list[tuple[bool, str]],
    ) -> None:
        self._agent_id = agent_id
        self._outcomes = list(outcomes)
        self.calls = 0

    @property
    def id(self) -> str:
        return self._agent_id

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

    def quota(self, task_class: str):
        from e2e_ai.agents.capabilities import QUOTA_READY
        from e2e_ai.agents.quota import QuotaSnapshot

        _ = task_class
        return QuotaSnapshot(plugin_id=self.id, state=QUOTA_READY)

    def supports_playwright_mcp(self) -> bool:
        return False

    def _next(self, request):
        self.calls += 1
        ok, text = self._outcomes.pop(0)
        from e2e_ai.agents.capabilities import AgentResult
        from e2e_ai.agents.invocation import classify_agent_exit

        exit_class = classify_agent_exit(0 if ok else 1, text, "")
        return AgentResult(
            agent_id=self.id,
            exit_code=0 if ok else 1,
            stdout=text,
            stderr="",
            exit_class=exit_class,
        )

    def plan(self, request):
        return self._next(request)

    def implement(self, request):
        return self._next(request)

    def instrument(self, request):
        return self._next(request)


class _Bound:
    def __init__(self, plugin: _ScriptedAgent) -> None:
        self._plugin = plugin

    @property
    def id(self) -> str:
        return self._plugin.id

    def run(
        self,
        prompt,
        *,
        workdir,
        timeout,
        log_dir=None,
        env=None,
        mcp=None,
    ):
        from e2e_ai.agents.base import AgentRunResult

        _ = workdir, timeout, log_dir, env, mcp
        request = type("R", (), {"prompt": prompt})()
        if "instrumentation agent" in prompt:
            result = self._plugin.instrument(request)
        elif "implementer agent" in prompt:
            result = self._plugin.implement(request)
        else:
            result = self._plugin.plan(request)
        return AgentRunResult(
            result.agent_id,
            result.exit_code,
            result.stdout,
            result.stderr,
            exit_class=result.exit_class,
        )


def _scripted_registry(
    plugins: dict[str, _ScriptedAgent],
    config: EffectiveConfig,
) -> AgentRegistry:
    return AgentRegistry(cast(dict[str, AgentPlugin], plugins), config)


class TestProviderPool:
    def test_explicit_role_preferences(self) -> None:
        config = _config(planner=("codex", "claude", "cursor"))
        assert provider_pool(config, "planner") == (
            "codex",
            "claude",
            "cursor",
        )

    def test_select_provider_skips_excluded(self, monkeypatch) -> None:
        config = _config(planner=("codex", "claude"))
        plugins = create_agent_plugins(config)

        def always_ready(plugin, **_kwargs):
            return 100

        monkeypatch.setattr(
            "e2e_ai.agents.router._score_candidate",
            always_ready,
        )
        first = select_provider(config, "planner", "difficult", plugins)
        second = select_provider(
            config,
            "planner",
            "difficult",
            plugins,
            excluded={first.selected_provider},
            failover_retry=True,
        )
        assert first.selected_provider == "codex"
        assert second.selected_provider == "claude"
        assert second.failover_retry is True

    def test_select_provider_keeps_unknown_quota_for_failover(
        self,
        monkeypatch,
    ) -> None:
        config = _config(planner=("codex", "claude"))
        plugins = create_agent_plugins(config)

        # A logged-in provider with unknown quota must remain eligible once
        # Codex has been excluded after reporting quota exhaustion.
        monkeypatch.setattr(
            "e2e_ai.agents.router._score_candidate",
            lambda *_args, **_kwargs: -100,
        )
        selection = select_provider(
            config,
            "planner",
            "difficult",
            plugins,
            excluded={"codex"},
            failover_retry=True,
        )

        assert selection.selected_provider == "claude"

    def test_unknown_quota_is_eligible_for_failover(self) -> None:
        from e2e_ai.agents.capabilities import (
            QUOTA_READY,
            QUOTA_UNKNOWN,
            AgentCapabilities,
            AgentHealth,
        )
        from e2e_ai.agents.quota import QuotaSnapshot

        class UnknownQuotaAgent:
            @property
            def id(self) -> str:
                return "cursor"

            def check_login(self):
                return AgentHealth(
                    agent_id="cursor",
                    logged_in=True,
                    verified=True,
                    state=QUOTA_READY,
                )

            def discover(self):
                return AgentCapabilities(plugin_id="cursor", schema_mode=True)

            def quota(self, task_class: str):
                _ = task_class
                return QuotaSnapshot(
                    plugin_id="cursor",
                    state=QUOTA_UNKNOWN,
                    optimistic=False,
                )

            def plan(self, request):
                _ = request
                raise NotImplementedError

            def implement(self, request):
                _ = request
                raise NotImplementedError

            def instrument(self, request):
                _ = request
                raise NotImplementedError

            def supports_playwright_mcp(self) -> bool:
                return False

        score = _score_candidate(
            UnknownQuotaAgent(),
            task_class="difficult",
            require_schema=True,
            routing_allow_unknown=False,
        )

        assert score == -100


class TestRoutingOutcomes:
    def test_auth_error_switches_provider(self) -> None:
        config = _config()
        action = decide_routing_action(
            EXIT_AUTH_ERROR,
            config=config,
            providers_remaining=True,
            switches_remaining=True,
        )
        assert action is RoutingAction.SWITCH_PROVIDER

    def test_external_blocker_stops(self) -> None:
        config = _config()
        action = decide_routing_action(
            EXIT_QUOTA_ERROR,
            config=config,
            external_blocker=True,
        )
        assert action is RoutingAction.EXTERNAL_BLOCKER

    def test_non_retryable_stops(self) -> None:
        config = _config()
        action = decide_routing_action(
            "task_failure",
            config=config,
            providers_remaining=True,
            switches_remaining=True,
        )
        assert action is RoutingAction.STOP_TEST

    def test_budget_exhausted_stops(self) -> None:
        config = _config()
        action = decide_routing_action(
            EXIT_EMPTY_OUTPUT,
            config=config,
            providers_remaining=True,
            switches_remaining=False,
        )
        assert action is RoutingAction.STOP_TEST


class TestFailoverLoop:
    def test_planner_switches_after_retryable_failure(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        config = evolve(
            _config(planner=("codex", "claude")),
            project_root=tmp_path,
            state_dir=tmp_path / ".e2e-ai",
        )
        db = tmp_path / "state.sqlite3"
        conn = ensure_database(db)
        _seed_project(conn)
        run_id = create_repair_run(conn, config)

        codex = _ScriptedAgent("codex", [(False, "not logged in")])
        claude = _ScriptedAgent("claude", [(True, "fix the button")])
        registry = _scripted_registry(
            {"codex": codex, "claude": claude, "cursor": claude},
            config,
        )

        monkeypatch.setattr(
            "e2e_ai.orchestrator.loop.bind_role",
            lambda cfg, role, plugins, plugin_id=None: _Bound(
                plugins[plugin_id or "codex"]
            ),
        )

        outcome = _invoke_role_with_failover(
            registry,
            "planner",
            "planner prompt",
            config=config,
            run_id=run_id,
            test_id="t1",
            conn=conn,
            log_dir=tmp_path / "logs",
            context=None,
            lease=None,
            failover=FailoverTracker(),
        )
        assert outcome.ok is True
        assert outcome.text == "fix the button"
        assert codex.calls == 1
        assert claude.calls == 1

        rows = conn.execute(
            "SELECT agent_id, failover_retry, exit_class, switch_reason "
            "FROM agent_invocations ORDER BY started_at"
        ).fetchall()
        assert len(rows) == 2
        assert rows[0]["agent_id"] == "codex"
        assert rows[0]["failover_retry"] == 0
        assert rows[1]["agent_id"] == "claude"
        assert rows[1]["failover_retry"] == 1
        conn.close()

    def test_implementer_switches_after_retryable_failure(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        config = evolve(
            _config(implementer=("claude", "codex")),
            project_root=tmp_path,
            state_dir=tmp_path / ".e2e-ai",
        )
        db = tmp_path / "state.sqlite3"
        conn = ensure_database(db)
        _seed_project(conn)
        run_id = create_repair_run(conn, config)

        claude = _ScriptedAgent("claude", [(False, "rate limit exceeded")])
        codex = _ScriptedAgent("codex", [(True, "implemented")])
        registry = _scripted_registry(
            {"codex": codex, "claude": claude, "cursor": claude},
            config,
        )

        monkeypatch.setattr(
            "e2e_ai.orchestrator.loop.bind_role",
            lambda cfg, role, plugins, plugin_id=None: _Bound(
                plugins[plugin_id or "codex"]
            ),
        )
        monkeypatch.setattr(
            "e2e_ai.orchestrator.loop._working_tree_changed",
            lambda _root: True,
        )

        outcome = _invoke_role_with_failover(
            registry,
            "implementer",
            "implementer agent prompt",
            config=config,
            run_id=run_id,
            test_id="t1",
            conn=conn,
            log_dir=tmp_path / "logs",
            context=None,
            lease=None,
            failover=FailoverTracker(),
        )
        assert outcome.ok is True
        assert claude.calls == 1
        assert codex.calls == 1
        conn.close()

    def test_failover_budget_stops_rotation(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        config = evolve(
            _config(
                planner=("codex", "claude", "cursor"),
                max_switches=1,
            ),
            project_root=tmp_path,
            state_dir=tmp_path / ".e2e-ai",
        )
        db = tmp_path / "state.sqlite3"
        conn = ensure_database(db)
        _seed_project(conn)
        run_id = create_repair_run(conn, config)

        plugins = {
            "codex": _ScriptedAgent("codex", [(False, "not logged in")]),
            "claude": _ScriptedAgent("claude", [(False, "not logged in")]),
            "cursor": _ScriptedAgent("cursor", [(False, "not logged in")]),
        }
        registry = _scripted_registry(plugins, config)

        monkeypatch.setattr(
            "e2e_ai.orchestrator.loop.bind_role",
            lambda cfg, role, plugins, plugin_id=None: _Bound(
                plugins[plugin_id or "codex"]
            ),
        )

        outcome = _invoke_role_with_failover(
            registry,
            "planner",
            "planner prompt",
            config=config,
            run_id=run_id,
            test_id="t1",
            conn=conn,
            log_dir=tmp_path / "logs",
            context=None,
            lease=None,
            failover=FailoverTracker(),
        )
        assert outcome.ok is False
        assert outcome.routing_action is RoutingAction.STOP_TEST
        count = conn.execute("SELECT COUNT(*) FROM agent_invocations").fetchone()[0]
        assert count == 2
        conn.close()


class TestInvocationHistory:
    def test_records_provider_order_and_switch_reason(self, tmp_path: Path) -> None:
        db = tmp_path / "state.sqlite3"
        conn = ensure_database(db)
        _seed_project(conn)
        config = evolve(
            _config(),
            project_root=tmp_path,
            state_dir=tmp_path / ".e2e-ai",
        )
        run_id = create_repair_run(conn, config)
        record_agent_invocation(
            conn,
            run_id=run_id,
            role="planner",
            agent_id="codex",
            command=["codex", "agent_1"],
            status="error",
            exit_code=1,
            test_id="t1",
            provider_order=["codex", "claude"],
            exit_class=EXIT_AUTH_ERROR,
            switch_reason=None,
            failover_retry=False,
        )
        record_agent_invocation(
            conn,
            run_id=run_id,
            role="planner",
            agent_id="claude",
            command=["claude", "agent_2"],
            status="ok",
            exit_code=0,
            test_id="t1",
            provider_order=["codex", "claude"],
            exit_class=None,
            switch_reason="failover from codex",
            failover_retry=True,
        )
        rows = conn.execute(
            "SELECT "
            "agent_id, provider_order_json, switch_reason, failover_retry "
            "FROM agent_invocations ORDER BY started_at"
        ).fetchall()
        assert rows[0]["agent_id"] == "codex"
        assert rows[1]["switch_reason"] == "failover from codex"
        assert rows[1]["failover_retry"] == 1
        conn.close()
