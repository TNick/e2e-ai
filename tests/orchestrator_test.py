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
from e2e_ai.orchestrator.decisions import (
    should_escalate_to_instrumentation,
    should_stop_test,
)
from e2e_ai.orchestrator.loop import (
    _will_start_docker_containers,
    run_repair_loop,
)
from e2e_ai.orchestrator.models import (
    STATE_FAILED,
    STATE_INSTRUMENTING,
    STATE_PASSED,
    STATE_PLANNING,
    STATE_REGRESSED,
    STATE_RUNNING,
)
from e2e_ai.orchestrator.prompts import (
    MAX_IMPLEMENTER_PLAN_CHARS,
    _truncate_implementer_plan,
    build_planner_prompt,
)
from e2e_ai.orchestrator.state_machine import (
    EVENT_FAIL,
    EVENT_INSTRUMENT,
    EVENT_PASS,
    EVENT_PLAN,
    EVENT_START,
    InvalidTransitionError,
)
from e2e_ai.orchestrator.store import (
    attempt_history_counts,
    format_test_history_suffix,
    record_repair_plan,
)
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


def _result(
    tmp_path: Path, *, passed: bool, attempt_index: int = 0
) -> RunResult:
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


class _FailingPlannerAgent(_FakeAgent):
    def plan(self, request):
        self.calls.append(request.prompt)
        from e2e_ai.agents.capabilities import AgentResult
        from e2e_ai.agents.invocation import classify_agent_exit

        return AgentResult(
            agent_id=self.id,
            exit_code=1,
            stdout="",
            stderr="planner bombed",
            exit_class=classify_agent_exit(1, "", "planner bombed"),
        )


class _FakeRegistry:
    def __init__(self) -> None:
        self._plugins = {
            "claude": _FakeAgent("claude"),
            "codex": _FakeAgent("codex"),
            "claude-strong": _FakeAgent(
                "claude-strong", "add logging then fix"
            ),
        }
        self.agents = {
            "planner": _LegacyBound(self._plugins["claude"]),
            "implementer": _LegacyBound(self._plugins["codex"]),
            "instrumenter": _LegacyBound(self._plugins["claude-strong"]),
        }
        self._config = None

    def role(self, role: str):
        return self.agents[role]


class _LegacyBound:
    def __init__(self, plugin: _FakeAgent) -> None:
        self._plugin = plugin

    @property
    def id(self) -> str:
        return self._plugin.id

    def run(
        self, prompt, *, workdir, timeout, log_dir=None, env=None, mcp=None
    ):
        _ = workdir, timeout, log_dir, env, mcp
        if "instrumentation agent" in prompt:
            result = self._plugin.instrument(
                type("R", (), {"prompt": prompt})()
            )
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
        assert (
            next_state(STATE_REGRESSED, EVENT_INSTRUMENT) == STATE_INSTRUMENTING
        )

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
            failure=FailureInfo(
                error_message="Cannot connect to the Docker daemon"
            ),
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
        registry._config = config
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


class TestDockerStartupMessages:
    def test_will_start_docker_for_postgres_isolation(self, tmp_path):
        from attrs import evolve

        base = _config(tmp_path)
        config = evolve(
            base,
            isolation=evolve(base.isolation, backend="docker_postgres"),
        )
        assert _will_start_docker_containers(config, start_runtime=False)
        assert not _will_start_docker_containers(
            evolve(
                config,
                isolation=evolve(config.isolation, backend="none"),
            ),
            start_runtime=False,
        )

    def test_will_start_docker_for_compose_target_runtime(self, tmp_path):
        from attrs import evolve

        base = _config(tmp_path)
        config = evolve(
            base,
            target_runtime=evolve(
                base.target_runtime, backend="docker_compose"
            ),
        )
        assert _will_start_docker_containers(config, start_runtime=True)
        assert not _will_start_docker_containers(config, start_runtime=False)

    def test_repair_loop_reports_docker_startup(self, tmp_path, monkeypatch):
        from attrs import evolve

        base = _config(tmp_path)
        config = evolve(
            base,
            isolation=evolve(base.isolation, backend="docker_postgres"),
        )
        conn = _seeded_conn(config)
        registry = _FakeRegistry()
        messages: list[str] = []

        def fake(config, request, **kwargs):
            _ = config, request, kwargs
            return _result(tmp_path, passed=True), None

        monkeypatch.setattr("e2e_ai.orchestrator.loop.run_attempt", fake)
        run_repair_loop(
            config,
            conn,
            registry,
            isolation=_FakeIsolation(),
            reporter=messages.append,
            start_runtime=False,
        )
        assert messages[0] == "Scheduling 1 test(s)."
        assert messages[1] == "Starting Docker containers..."
        conn.close()

    def test_keyboard_interrupt_marks_run_stopped(self, tmp_path, monkeypatch):
        from e2e_ai.repair.stale_runs import REASON_PROCESS_INTERRUPTED

        config = _config(tmp_path)
        conn = _seeded_conn(config)

        def fake_run(config, request, **kwargs):
            _ = config, request, kwargs
            raise KeyboardInterrupt

        monkeypatch.setattr("e2e_ai.orchestrator.loop.run_attempt", fake_run)
        messages: list[str] = []
        summary = run_repair_loop(
            config,
            conn,
            _FakeRegistry(),
            isolation=_FakeIsolation(),
            reporter=messages.append,
            start_runtime=False,
        )

        assert summary.interrupted is True
        assert summary.status == "stopped"
        assert summary.reason == REASON_PROCESS_INTERRUPTED
        assert "Interrupted." in messages
        row = conn.execute(
            "SELECT status, reason FROM runs WHERE id = ?",
            (summary.run_id,),
        ).fetchone()
        assert row["status"] == "stopped"
        assert row["reason"] == REASON_PROCESS_INTERRUPTED
        conn.close()

    def test_failed_test_invokes_planner_then_implementer(
        self, tmp_path, monkeypatch
    ):
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

    def test_planner_failure_retries_without_invalid_transition(
        self,
        tmp_path,
        monkeypatch,
    ):
        config = _config(tmp_path)
        conn = _seeded_conn(config)
        failing_planner = _FailingPlannerAgent("claude")
        registry = _FakeRegistry()
        registry._plugins["claude"] = failing_planner
        registry.agents["planner"] = _LegacyBound(failing_planner)
        seq = [(False, FailureInfo(error_message="boom"))] * 3
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
        assert len(failing_planner.calls) == 2
        conn.close()

    def test_second_failure_invokes_instrumentation(
        self, tmp_path, monkeypatch
    ):
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
        assert any(
            "instrumentation agent" in call
            for call in registry._plugins["claude"].calls
        )
        conn.close()

    def test_regression_gets_previous_failure_context(
        self, tmp_path, monkeypatch
    ):
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
        assert state.has_prior_pass
        context = build_repair_context(conn, packet)
        prompt = build_planner_prompt(context, config=config, test=TEST)
        assert "old plan that failed" in prompt
        assert "FAILED PLAN" in prompt
        conn.close()


class TestImplementerPromptSizing:
    def test_truncates_oversized_plan_with_head_and_tail(self) -> None:
        plan = "start\n" + ("x" * (MAX_IMPLEMENTER_PLAN_CHARS + 1)) + "\nend"

        compact = _truncate_implementer_plan(plan)

        assert len(compact) <= MAX_IMPLEMENTER_PLAN_CHARS + 100
        assert compact.startswith("start\n")
        assert compact.endswith("\nend")
        assert "plan characters omitted" in compact


class TestTestHistory:
    def test_format_test_history_suffix(self) -> None:
        assert format_test_history_suffix(3, 1) == " (3 runs, 1 failures)"

    def test_history_counts_from_database(self, tmp_path) -> None:
        config = _config(tmp_path)
        conn = _seeded_conn(config)
        run_id = create_repair_run(conn, config)
        passed = _result(tmp_path, passed=True)
        create_attempt_record(
            conn,
            attempt_id=passed.attempt_id,
            run_id=run_id,
            test_id=TEST.id,
            attempt_index=0,
            work_dir=str(passed.work_dir),
        )
        finish_attempt_record(conn, passed)
        failed = _result(tmp_path, passed=False, attempt_index=1)
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
            failure=FailureInfo(error_message="boom"),
        )
        insert_failure_packet(conn, packet)

        assert attempt_history_counts(conn, TEST.id) == (2, 1)
        conn.close()

    def test_run_one_test_prints_history_suffix(
        self,
        tmp_path,
        monkeypatch,
    ) -> None:
        config = _config(tmp_path)
        conn = _seeded_conn(config)
        registry = _FakeRegistry()
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

        def fake_run(config, request, **kwargs):
            _ = config, request, kwargs
            return _result(tmp_path, passed=True, attempt_index=1), None

        monkeypatch.setattr("e2e_ai.orchestrator.loop.run_attempt", fake_run)
        messages: list[str] = []

        run_one_test_until_resolved(
            conn=conn,
            config=config,
            test=TEST,
            run_id=run_id,
            isolation=_FakeIsolation(),
            agents=registry._plugins,
            registry=registry,
            reporter=messages.append,
        )
        assert any("(1 runs, 0 failures)" in message for message in messages)
        conn.close()


class TestFailedOnlyRepair:
    """``only_failed`` limits repair to tests failed in the previous run."""

    OTHER = DiscoveredTest(
        id="demo_other123",
        title="other thing",
        spec_file="b.spec.ts",
        project_name="chromium",
        line=8,
        raw_list_line="b.spec.ts › other thing",
    )

    def test_only_failed_schedules_previous_failures(
        self, tmp_path, monkeypatch
    ):
        config = _config(tmp_path)
        conn = ensure_database(database_path(config))
        refresh_inventory(conn, config, Inventory(tests=(TEST, self.OTHER)))
        store = RepairStore(conn)
        prev_run = store.start_run(config.project_id, reason="seed")
        passed = _result(tmp_path, passed=True)
        failed = _result(tmp_path, passed=False)
        create_attempt_record(
            conn,
            run_id=prev_run,
            test_id=TEST.id,
            attempt_index=0,
            work_dir=str(passed.work_dir),
            attempt_id=passed.attempt_id,
        )
        finish_attempt_record(conn, passed)
        create_attempt_record(
            conn,
            run_id=prev_run,
            test_id=self.OTHER.id,
            attempt_index=0,
            work_dir=str(failed.work_dir),
            attempt_id=failed.attempt_id,
        )
        finish_attempt_record(conn, failed)
        store.finish_run(prev_run, status="failed")
        empty_run = store.start_run(config.project_id, reason="empty")
        store.finish_run(empty_run, status="failed")

        scheduled: list[str] = []
        registry = _FakeRegistry()

        def fake_run(config, request, **kwargs):
            scheduled.append(request.test_id)
            return _result(tmp_path, passed=True), None

        monkeypatch.setattr("e2e_ai.orchestrator.loop.run_attempt", fake_run)
        messages: list[str] = []
        run_repair_loop(
            config,
            conn,
            registry,
            isolation=_FakeIsolation(),
            reporter=messages.append,
            only_failed=True,
            start_runtime=False,
        )
        assert scheduled == [self.OTHER.id]
        assert messages[0].startswith(
            f"Scheduling 1 test(s) that failed in run {prev_run}."
        )
        conn.close()

    def test_only_failed_no_failures_does_not_create_empty_run(self, tmp_path):
        config = _config(tmp_path)
        conn = ensure_database(database_path(config))
        refresh_inventory(conn, config, Inventory(tests=(TEST,)))
        store = RepairStore(conn)
        prev_run = store.start_run(config.project_id, reason="seed")
        passed = _result(tmp_path, passed=True)
        create_attempt_record(
            conn,
            run_id=prev_run,
            test_id=TEST.id,
            attempt_index=0,
            work_dir=str(passed.work_dir),
            attempt_id=passed.attempt_id,
        )
        finish_attempt_record(conn, passed)
        store.finish_run(prev_run, status="passed")

        messages: list[str] = []
        summary = run_repair_loop(
            config,
            conn,
            _FakeRegistry(),
            isolation=_FakeIsolation(),
            reporter=messages.append,
            only_failed=True,
            start_runtime=False,
        )

        run_count = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        assert summary.all_green
        assert summary.run_id is None
        assert run_count == 1
        assert messages == [
            f"Scheduling 0 test(s) that failed in run {prev_run}."
        ]
        conn.close()


class TestRepairBudget:
    """Repair attempt budgets are scoped to the current run."""

    def test_historical_failures_do_not_exhaust_new_run(self, tmp_path):
        config = _config(tmp_path)
        conn = ensure_database(database_path(config))
        refresh_inventory(conn, config, Inventory(tests=(TEST,)))
        store = RepairStore(conn)
        old_run = store.start_run(config.project_id, reason="old")
        new_run = store.start_run(config.project_id, reason="new")

        for index in range(3):
            failed = _result(tmp_path, passed=False, attempt_index=index)
            create_attempt_record(
                conn,
                run_id=old_run,
                test_id=TEST.id,
                attempt_index=index,
                work_dir=str(failed.work_dir),
                attempt_id=failed.attempt_id,
            )
            finish_attempt_record(conn, failed)

        current = _result(tmp_path, passed=False, attempt_index=0)
        create_attempt_record(
            conn,
            run_id=new_run,
            test_id=TEST.id,
            attempt_index=0,
            work_dir=str(current.work_dir),
            attempt_id=current.attempt_id,
        )
        finish_attempt_record(conn, current)

        assert not should_stop_test(conn, TEST.id, 3, run_id=new_run)
        assert should_stop_test(conn, TEST.id, 3)
        conn.close()


class TestInstrumentationEscalation:
    """Instrumentation escalation is scoped to the current run."""

    def _failed_attempt(
        self,
        tmp_path: Path,
        conn: sqlite3.Connection,
        *,
        run_id: str,
        attempt_index: int,
        error_message: str = "assertion failed",
    ):
        failed = _result(
            tmp_path,
            passed=False,
            attempt_index=attempt_index,
        )
        create_attempt_record(
            conn,
            run_id=run_id,
            test_id=TEST.id,
            attempt_index=attempt_index,
            work_dir=str(failed.work_dir),
            attempt_id=failed.attempt_id,
        )
        finish_attempt_record(conn, failed)
        packet = build_failure_packet(
            test=TEST,
            attempt=failed,
            report=None,
            failure=FailureInfo(error_message=error_message),
        )
        insert_failure_packet(conn, packet)
        return failed, packet

    def test_historical_plans_do_not_escalate_new_run(self, tmp_path):
        config = _config(tmp_path)
        conn = ensure_database(database_path(config))
        refresh_inventory(conn, config, Inventory(tests=(TEST,)))
        store = RepairStore(conn)
        old_run = store.start_run(config.project_id, reason="old")
        new_run = store.start_run(config.project_id, reason="new")

        _, first_packet = self._failed_attempt(
            tmp_path, conn, run_id=old_run, attempt_index=0
        )
        record_repair_plan(
            conn,
            test_id=TEST.id,
            failure_packet_id=first_packet.id,
            agent_id="claude",
            plan_text="old plan",
        )
        self._failed_attempt(tmp_path, conn, run_id=old_run, attempt_index=1)
        _, new_packet = self._failed_attempt(
            tmp_path, conn, run_id=new_run, attempt_index=0
        )

        assert not should_escalate_to_instrumentation(
            conn,
            TEST.id,
            new_packet.signature,
            run_id=new_run,
        )
        conn.close()

    def test_escalates_after_fix_attempt_in_same_run(self, tmp_path):
        config = _config(tmp_path)
        conn = ensure_database(database_path(config))
        refresh_inventory(conn, config, Inventory(tests=(TEST,)))
        store = RepairStore(conn)
        run_id = store.start_run(config.project_id, reason="repair")

        _, first_packet = self._failed_attempt(
            tmp_path, conn, run_id=run_id, attempt_index=0
        )
        record_repair_plan(
            conn,
            test_id=TEST.id,
            failure_packet_id=first_packet.id,
            agent_id="claude",
            plan_text="first plan",
        )
        _, second_packet = self._failed_attempt(
            tmp_path, conn, run_id=run_id, attempt_index=1
        )

        assert first_packet.signature == second_packet.signature
        assert should_escalate_to_instrumentation(
            conn,
            TEST.id,
            second_packet.signature,
            run_id=run_id,
        )
        conn.close()


class TestTargetScopePrompts:
    """Repair prompts include configured edit scope."""

    def test_planner_prompt_includes_frontend_only_scope(
        self, tmp_path: Path
    ) -> None:
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

        reason = plan_is_blocked(
            "BLOCKED_REFERENCE_BACKEND: needs API schema change"
        )
        assert reason == "needs API schema change"
