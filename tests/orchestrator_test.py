"""Tests for the repair-loop orchestrator and state machine."""

from __future__ import annotations

import secrets
import sqlite3
import textwrap
from pathlib import Path

import pytest

from e2e_ai.agents.base import AgentRunResult
from e2e_ai.analysis import build_failure_packet, insert_failure_packet
from e2e_ai.analysis.context import build_repair_context
from e2e_ai.config import load_effective_config
from e2e_ai.db import database_path, ensure_database
from e2e_ai.inventory.models import DiscoveredTest
from e2e_ai.inventory.models import TestInventory as Inventory
from e2e_ai.inventory.store import refresh_inventory
from e2e_ai.models import FailureInfo
from e2e_ai.orchestrator import (
    classify_external_blocker,
    create_repair_run,
    next_state,
    run_one_test_until_resolved,
    validate_transition,
)
from e2e_ai.orchestrator.models import (
    STATE_FAILED,
    STATE_PASSED,
    STATE_PLANNING,
    STATE_RUNNING,
)
from e2e_ai.orchestrator.prompts import build_planner_prompt
from e2e_ai.orchestrator.state_machine import (
    EVENT_FAIL,
    EVENT_PASS,
    EVENT_PLAN,
    EVENT_START,
    InvalidTransitionError,
)
from e2e_ai.orchestrator.store import record_repair_plan
from e2e_ai.repair.store import RepairStore
from e2e_ai.runner import TestRunResult as RunResult
from e2e_ai.runner import create_attempt_record, finish_attempt_record

PROJECT_YAML = textwrap.dedent(
    """
    project: {id: demo}
    state: {dir: .e2e-ai}
    playwright:
      cwd: e2e
      list_command: [echo, list]
      run_command: [echo, run]
    exclude: {tests: []}
    agents:
      planner: {plugin: claude}
      implementer: {plugin: codex}
      instrumenter: {plugin: claude}
    """
)

TEST = DiscoveredTest(
    id="demo_abc123",
    title="does a thing",
    spec_file="a.spec.ts",
    project_name="chromium",
    line=5,
    raw_list_line="a.spec.ts › does a thing",
)


def _config(tmp_path: Path):
    (tmp_path / "e2e").mkdir()
    (tmp_path / "e2e-ai.yml").write_text(PROJECT_YAML, encoding="utf-8")
    return load_effective_config(tmp_path)


def _seeded_conn(config) -> sqlite3.Connection:
    conn = ensure_database(database_path(config))
    refresh_inventory(conn, config, Inventory(tests=(TEST,)))
    conn.commit()
    return conn


def _result(tmp_path: Path, *, passed: bool, attempt_index: int = 0) -> RunResult:
    stamp = secrets.token_hex(4)
    work = tmp_path / stamp
    work.mkdir(parents=True, exist_ok=True)
    return RunResult(
        attempt_id=f"att-{stamp}",
        test_id=TEST.id,
        status="passed" if passed else "failed",
        exit_code=0 if passed else 1,
        duration_seconds=0.1,
        stdout_path=work / "output.log",
        stderr_path=work / "output.log",
        json_report_path=work / "playwright-results.json",
        work_dir=work,
        attempt_index=attempt_index,
    )


class _FakeIsolation:
    def prepare_baseline(self, context) -> None:
        _ = context

    def create_environment(self, context, test, attempt_id):
        from e2e_ai.isolation.models import EnvironmentLease

        work = context.state_dir / "work" / test.id / attempt_id
        work.mkdir(parents=True, exist_ok=True)
        return EnvironmentLease(
            id=f"env-{attempt_id}",
            test_id=test.id,
            work_dir=work,
            env={},
        )

    def cleanup_environment(self, lease, outcome) -> None:
        _ = lease, outcome


class _FakeAgent:
    def __init__(self, agent_id: str, text: str = "do the thing") -> None:
        self.id = agent_id
        self.text = text
        self.calls: list[str] = []

    def check_login(self):
        raise NotImplementedError

    def discover(self):
        raise NotImplementedError

    def quota(self, task_class: str):
        raise NotImplementedError

    def plan(self, request):
        self.calls.append(request.prompt)
        from e2e_ai.agents.capabilities import AgentResult
        from e2e_ai.agents.invocation import classify_agent_exit

        return AgentResult(
            agent_id=self.id,
            exit_code=0,
            stdout=self.text,
            stderr="",
            exit_class=classify_agent_exit(0, self.text, ""),
        )

    def implement(self, request):
        self.calls.append(request.prompt)
        from e2e_ai.agents.capabilities import AgentResult
        from e2e_ai.agents.invocation import classify_agent_exit

        return AgentResult(
            agent_id=self.id,
            exit_code=0,
            stdout="implemented",
            stderr="",
            exit_class=classify_agent_exit(0, "", ""),
        )

    def instrument(self, request):
        self.calls.append(request.prompt)
        from e2e_ai.agents.capabilities import AgentResult
        from e2e_ai.agents.invocation import classify_agent_exit

        return AgentResult(
            agent_id=self.id,
            exit_code=0,
            stdout="add logging then fix",
            stderr="",
            exit_class=classify_agent_exit(0, "", ""),
        )


class _FakeRegistry:
    def __init__(self) -> None:
        self._plugins = {
            "claude": _FakeAgent("claude"),
            "codex": _FakeAgent("codex"),
            "claude-strong": _FakeAgent("claude-strong", "add logging then fix"),
        }
        self.agents = {
            "planner": _LegacyBound(self._plugins["claude"]),
            "implementer": _LegacyBound(self._plugins["codex"]),
            "instrumenter": _LegacyBound(self._plugins["claude-strong"]),
        }

    def role(self, role: str):
        return self.agents[role]


class _LegacyBound:
    def __init__(self, plugin: _FakeAgent) -> None:
        self._plugin = plugin

    @property
    def id(self) -> str:
        return self._plugin.id

    def run(self, prompt, *, workdir, timeout, log_dir=None, env=None, mcp=None):
        _ = workdir, timeout, log_dir, env, mcp
        if "instrumentation agent" in prompt:
            result = self._plugin.instrument(type("R", (), {"prompt": prompt})())
        elif "implementer agent" in prompt:
            result = self._plugin.implement(type("R", (), {"prompt": prompt})())
        else:
            result = self._plugin.plan(type("R", (), {"prompt": prompt})())
        return AgentRunResult(
            self._plugin.id,
            result.exit_code,
            result.stdout,
            result.stderr,
        )


class TestStateMachine:
    def test_valid_transitions(self):
        assert next_state("pending", EVENT_START) == STATE_RUNNING
        assert next_state(STATE_RUNNING, EVENT_PASS) == STATE_PASSED
        assert next_state(STATE_RUNNING, EVENT_FAIL) == STATE_FAILED
        assert next_state(STATE_FAILED, EVENT_PLAN) == STATE_PLANNING

    def test_invalid_transition_raises(self):
        with pytest.raises(InvalidTransitionError):
            validate_transition(STATE_PASSED, EVENT_START)


class TestDecisions:
    def test_external_blocker_for_missing_docker(self, tmp_path):
        config = _config(tmp_path)
        from attrs import evolve

        config = evolve(
            config,
            isolation=evolve(config.isolation, backend="docker_postgres"),
        )
        packet = build_failure_packet(
            test=TEST,
            attempt=_result(tmp_path, passed=False),
            report=None,
            failure=FailureInfo(error_message="Cannot connect to the Docker daemon"),
        )
        assert classify_external_blocker(packet, config=config)

    def test_product_failure_not_external_by_default(self, tmp_path):
        packet = build_failure_packet(
            test=TEST,
            attempt=_result(tmp_path, passed=False),
            report=None,
            failure=FailureInfo(error_message="expect(received).toBeVisible()"),
        )
        assert not classify_external_blocker(packet)


class TestLoop:
    def test_passed_test_records_success(self, tmp_path, monkeypatch):
        config = _config(tmp_path)
        conn = _seeded_conn(config)
        registry = _FakeRegistry()
        outcomes = [(True, None)]

        def fake(config, request, **kwargs):
            passed, failure = next(iter(outcomes))
            return _result(tmp_path, passed=passed), failure

        monkeypatch.setattr("e2e_ai.orchestrator.loop.run_attempt", fake)
        run_id = create_repair_run(conn, config)
        state = run_one_test_until_resolved(
            conn=conn,
            config=config,
            test=TEST,
            run_id=run_id,
            isolation=_FakeIsolation(),
            agents=registry._plugins,
            registry=registry,
        )
        assert state.state == STATE_PASSED
        row = conn.execute(
            "SELECT last_status FROM tests WHERE id = ?", (TEST.id,)
        ).fetchone()
        assert row["last_status"] == "passing"
        conn.close()

    def test_failed_test_invokes_planner_then_implementer(self, tmp_path, monkeypatch):
        config = _config(tmp_path)
        conn = _seeded_conn(config)
        registry = _FakeRegistry()
        seq = [
            (False, FailureInfo(error_message="boom")),
            (True, None),
        ]
        it = iter(seq)

        def fake_run(config, request, **kwargs):
            passed, failure = next(it)
            return _result(tmp_path, passed=passed), failure

        monkeypatch.setattr("e2e_ai.orchestrator.loop.run_attempt", fake_run)
        run_id = create_repair_run(conn, config)
        state = run_one_test_until_resolved(
            conn=conn,
            config=config,
            test=TEST,
            run_id=run_id,
            isolation=_FakeIsolation(),
            agents=registry._plugins,
            registry=registry,
        )
        assert state.state == STATE_PASSED
        assert registry._plugins["claude"].calls
        assert registry._plugins["codex"].calls
        plans = RepairStore(conn).previous_plans(TEST.id)
        assert plans and plans[0].outcome == "implemented"
        conn.close()

    def test_second_failure_invokes_instrumentation(self, tmp_path, monkeypatch):
        config = _config(tmp_path)
        conn = _seeded_conn(config)
        registry = _FakeRegistry()
        seq = [(False, FailureInfo(error_message="still broken"))] * 3
        it = iter(seq)

        def fake_run(config, request, **kwargs):
            passed, failure = next(it)
            return _result(tmp_path, passed=passed), failure

        monkeypatch.setattr("e2e_ai.orchestrator.loop.run_attempt", fake_run)
        run_id = create_repair_run(conn, config)
        state = run_one_test_until_resolved(
            conn=conn,
            config=config,
            test=TEST,
            run_id=run_id,
            isolation=_FakeIsolation(),
            agents=registry._plugins,
            registry=registry,
        )
        assert state.state == "exhausted_attempts"
        assert registry._plugins["claude-strong"].calls
        conn.close()

    def test_regression_gets_previous_failure_context(self, tmp_path, monkeypatch):
        config = _config(tmp_path)
        conn = _seeded_conn(config)
        registry = _FakeRegistry()

        # Seed a prior passing attempt and a failed plan.
        run_id = create_repair_run(conn, config)
        prior = _result(tmp_path, passed=True)
        create_attempt_record(
            conn,
            attempt_id=prior.attempt_id,
            run_id=run_id,
            test_id=TEST.id,
            attempt_index=0,
            work_dir=str(prior.work_dir),
        )
        finish_attempt_record(conn, prior)
        failed = _result(tmp_path, passed=False)
        create_attempt_record(
            conn,
            attempt_id=failed.attempt_id,
            run_id=run_id,
            test_id=TEST.id,
            attempt_index=1,
            work_dir=str(failed.work_dir),
        )
        finish_attempt_record(conn, failed)
        packet = build_failure_packet(
            test=TEST,
            attempt=failed,
            report=None,
            failure=FailureInfo(error_message="regressed"),
        )
        insert_failure_packet(conn, packet)
        record_repair_plan(
            conn,
            test_id=TEST.id,
            failure_packet_id=packet.id,
            agent_id="claude",
            plan_text="old plan that failed",
        )

        seq = [(False, FailureInfo(error_message="regressed again"))]
        it = iter(seq)

        def fake_run(config, request, **kwargs):
            passed, failure = next(it)
            return _result(tmp_path, passed=passed), failure

        monkeypatch.setattr("e2e_ai.orchestrator.loop.run_attempt", fake_run)
        state = run_one_test_until_resolved(
            conn=conn,
            config=config,
            test=TEST,
            run_id=run_id,
            isolation=_FakeIsolation(),
            agents=registry._plugins,
            registry=registry,
            dry_run_agents=True,
        )
        assert state.is_regression
        context = build_repair_context(conn, packet)
        prompt = build_planner_prompt(context, config=config, test=TEST)
        assert "old plan that failed" in prompt
        assert "FAILED PLAN" in prompt
        conn.close()


class TestTargetScopePrompts:
    """Repair prompts include configured edit scope."""

    def test_planner_prompt_includes_frontend_only_scope(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        conn = _seeded_conn(config)
        packet = build_failure_packet(
            test=TEST,
            attempt=_result(tmp_path, passed=False),
            report=None,
            failure=FailureInfo(error_message="boom"),
        )
        context = build_repair_context(conn, packet, test=TEST, config=config)
        prompt = build_planner_prompt(context, config=config, test=TEST)
        assert "Target scope: frontend_only" in prompt
        assert "Only edit frontend surfaces" in prompt
        conn.close()

    def test_plan_is_blocked_reference_backend(self) -> None:
        from e2e_ai.planner import plan_is_blocked

        reason = plan_is_blocked("BLOCKED_REFERENCE_BACKEND: needs API schema change")
        assert reason == "needs API schema change"
