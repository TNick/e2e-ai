"""Tests for runtime refresh planning and execution."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from e2e_ai.config import load_effective_config
from e2e_ai.errors import ConfigError
from e2e_ai.runtime.docker_compose import create_docker_compose_runtime
from e2e_ai.runtime.models import RuntimeContext, RuntimeState
from e2e_ai.runtime.refresh import (
    actions_for_changed_paths,
    execute_runtime_refresh,
    path_matches_rule,
    plan_runtime_refresh,
)

REFRESH_YAML = textwrap.dedent(
    """
    project: {id: demo}
    state: {dir: .e2e-ai}
    playwright:
      cwd: e2e
      list_command: [echo, list]
      run_command: [echo, run]
    exclude: {tests: []}
    target_runtime:
      backend: docker_compose
      cwd: docker
      project_name: demo-runtime
      compose_files: [compose.yml]
      refresh:
        actions:
          frontend:
            description: Rebuild frontend
            compose:
              - [up, -d, --build, frontend]
          backend:
            description: Restart backend
            compose:
              - [restart, backend]
          db-seed:
            description: Re-seed database
            compose:
              - [build, db-seed]
              - [run, --rm, db-seed]
        rules:
          - paths: [frontend/**]
            actions: [frontend]
          - paths: [backend/**]
            actions: [backend]
          - paths: [docker/seed/**]
            actions: [db-seed]
      health_checks:
        - name: backend
          kind: http
          url: http://127.0.0.1:8000/api/health
          timeout_seconds: 2
    agents:
      planner: {plugin: codex}
    """
)


def _config(tmp_path: Path):
    docker = tmp_path / "docker"
    docker.mkdir()
    (docker / "compose.yml").write_text("services: {}\n", encoding="utf-8")
    (tmp_path / "e2e").mkdir()
    (tmp_path / "e2e-ai.yml").write_text(REFRESH_YAML, encoding="utf-8")
    return load_effective_config(tmp_path)


class TestRefreshPlanning:
    def test_path_rule_matches_nested_frontend_path(self) -> None:
        assert path_matches_rule("frontend/src/app.tsx", "frontend/**")

    def test_path_actions_follow_rule_order(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        refresh = config.target_runtime.docker_compose.refresh
        assert refresh is not None
        actions = actions_for_changed_paths(
            refresh,
            ["frontend/src/app.tsx"],
        )
        assert actions == ("frontend",)

    def test_plan_unions_path_and_agent_actions(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        refresh = config.target_runtime.docker_compose.refresh
        assert refresh is not None
        plan = plan_runtime_refresh(
            refresh,
            changed_paths=["e2e/helpers/foo.ts"],
            requested_actions=["backend"],
        )
        assert plan is not None
        assert plan.requested_actions == ("backend",)
        assert plan.selected_actions == ("backend",)

    def test_plan_merges_duplicate_actions_once(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        refresh = config.target_runtime.docker_compose.refresh
        assert refresh is not None
        plan = plan_runtime_refresh(
            refresh,
            changed_paths=["frontend/src/app.tsx"],
            requested_actions=["frontend"],
        )
        assert plan is not None
        assert plan.selected_actions == ("frontend",)

    def test_unknown_agent_actions_are_ignored(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        refresh = config.target_runtime.docker_compose.refresh
        assert refresh is not None
        plan = plan_runtime_refresh(
            refresh,
            changed_paths=[],
            requested_actions=["mystery-service"],
        )
        assert plan is None


class TestRefreshExecution:
    def test_executes_selected_compose_commands(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        config = _config(tmp_path)
        runtime = create_docker_compose_runtime(config)
        context = RuntimeContext(
            project_root=config.project_root,
            state_dir=config.state_dir,
            run_id="run-refresh",
            config=config,
            env={"COMPOSE_PROFILES": "e2e"},
        )
        state = RuntimeState(
            id="runtime-run-refresh",
            backend="docker_compose",
            work_dir=config.state_dir / "runs" / "run-refresh" / "runtime",
            env=dict(context.env),
        )
        state.work_dir.mkdir(parents=True, exist_ok=True)
        refresh = config.target_runtime.docker_compose.refresh
        assert refresh is not None
        plan = plan_runtime_refresh(
            refresh,
            changed_paths=["frontend/src/app.tsx"],
            requested_actions=[],
        )
        assert plan is not None
        calls: list[list[str]] = []

        def fake_run_compose(argv, *, cwd, env, log_path=None):
            calls.append(list(argv))
            return 0

        monkeypatch.setattr(
            "e2e_ai.isolation.docker_compose.run_compose",
            fake_run_compose,
        )
        monkeypatch.setattr(
            "e2e_ai.runtime.health.run_health_checks",
            lambda *_args, **_kwargs: None,
        )
        execution = execute_runtime_refresh(
            runtime,
            context=context,
            state=state,
            plan=plan,
        )
        assert execution.ok is True
        assert len(calls) == 1
        assert "frontend" in calls[0]
        report = json.loads(
            (state.work_dir / "refresh-report.json").read_text(encoding="utf-8")
        )
        assert report["selected_actions"] == ["frontend"]


class TestRefreshValidation:
    def test_rejects_unknown_rule_action(self, tmp_path: Path) -> None:
        (tmp_path / "e2e").mkdir()
        (tmp_path / "e2e-ai.yml").write_text(
            textwrap.dedent(
                """
                project: {id: demo}
                state: {dir: .e2e-ai}
                playwright:
                  cwd: e2e
                  list_command: [echo, list]
                  run_command: [echo, run]
                target_runtime:
                  backend: docker_compose
                  compose_files: [compose.yml]
                  refresh:
                    actions:
                      frontend:
                        compose:
                          - [restart, frontend]
                    rules:
                      - paths: [frontend/**]
                        actions: [missing]
                agents:
                  planner: {plugin: codex}
                """
            ),
            encoding="utf-8",
        )
        with pytest.raises(ConfigError, match="unknown actions"):
            load_effective_config(tmp_path)
