"""Tests for the sequential fix loop orchestration (runner/agents stubbed)."""

from __future__ import annotations

import secrets
import sqlite3
import textwrap
from pathlib import Path

import pytest

from e2e_ai.agents.base import AgentRunResult
from e2e_ai.config import load_effective_config
from e2e_ai.db import database_path, ensure_database
from e2e_ai.inventory.models import DiscoveredTest
from e2e_ai.inventory.models import TestInventory as Inventory
from e2e_ai.inventory.store import refresh_inventory
from e2e_ai.loop import FixLoop
from e2e_ai.loop import TestResult as Result
from e2e_ai.models import FailureInfo
from e2e_ai.repair.store import RepairStore
from e2e_ai.runner import TestRunResult as RunResult

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
    """Open the state DB with the test case already in the inventory."""

    conn = ensure_database(database_path(config))
    refresh_inventory(conn, config, Inventory(tests=(TEST,)))
    conn.commit()
    return conn


def _result(tmp_path: Path, *, passed: bool) -> RunResult:
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
        attempt_index=0,
    )


def _stub_run_attempt(monkeypatch, tmp_path, outcomes):
    it = iter(outcomes)

    def fake(config, request, **kwargs):
        passed, failure = next(it)
        return _result(tmp_path, passed=passed), failure

    monkeypatch.setattr("e2e_ai.orchestrator.loop.run_attempt", fake)


class _FakeAgent:
    def __init__(self, agent_id: str, plan_text: str = "do the thing") -> None:
        self.id = agent_id
        self.plan_text = plan_text
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
            stdout=self.plan_text,
            stderr="",
            exit_class=classify_agent_exit(0, self.plan_text, ""),
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
            stdout=self.plan_text,
            stderr="",
            exit_class=classify_agent_exit(0, "", ""),
        )


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
            exit_class=result.exit_class,
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


def _loop(config, conn, registry, **kwargs) -> FixLoop:
    fl = FixLoop(config, conn, registry, **kwargs)
    fl.ensure_dirs()
    return fl


def _fix(fl: FixLoop):
    run_id = fl.store.start_run("demo")
    return fl.fix_one_test(TEST, run_id=run_id)


def test_fixes_after_one_plan(tmp_path, monkeypatch):
    config = _config(tmp_path)
    conn = _seeded_conn(config)
    registry = _FakeRegistry()

    # Fail once, then pass after the implementer runs.
    _stub_run_attempt(
        monkeypatch,
        tmp_path,
        [(False, FailureInfo(error_message="boom")), (True, None)],
    )

    report = _fix(_loop(config, conn, registry))

    assert report.result is Result.PASSED
    assert report.attempts == 2
    assert registry._plugins["claude"].calls  # planner produced a plan
    assert registry._plugins["codex"].calls  # implementer applied it
    plans = RepairStore(conn).previous_plans(TEST.id)
    assert plans and plans[0].outcome == "implemented"
    conn.close()


def test_escalates_to_instrumenter_on_repeat_failure(tmp_path, monkeypatch):
    config = _config(tmp_path)
    conn = _seeded_conn(config)
    registry = _FakeRegistry()

    _stub_run_attempt(
        monkeypatch,
        tmp_path,
        [(False, FailureInfo(error_message="still broken"))] * 3,
    )

    report = _fix(_loop(config, conn, registry))

    assert report.result is Result.FAILED
    # 3 attempts (default): initial + after implement + after instrument.
    assert report.attempts == 3
    assert any(
        "instrumentation agent" in call for call in registry._plugins["claude"].calls
    )
    conn.close()


def test_environmental_failure_is_blocked(tmp_path, monkeypatch):
    config = _config(tmp_path)
    conn = _seeded_conn(config)
    registry = _FakeRegistry()

    _stub_run_attempt(
        monkeypatch,
        tmp_path,
        [
            (
                False,
                FailureInfo(error_message="Error: connect ECONNREFUSED 127.0.0.1:5432"),
            )
        ],
    )

    report = _fix(_loop(config, conn, registry))

    assert report.result is Result.BLOCKED
    assert not registry._plugins["claude"].calls
    conn.close()


@pytest.mark.parametrize("blocked_plan", ["BLOCKED: services are down"])
def test_planner_can_declare_blocked(tmp_path, monkeypatch, blocked_plan):
    config = _config(tmp_path)
    conn = _seeded_conn(config)
    registry = _FakeRegistry()
    registry.agents["planner"] = _LegacyBound(_FakeAgent("claude", blocked_plan))
    registry._plugins["claude"] = registry.agents["planner"]._plugin

    _stub_run_attempt(
        monkeypatch,
        tmp_path,
        [(False, FailureInfo(error_message="assertion failed"))],
    )

    report = _fix(_loop(config, conn, registry))

    assert report.result is Result.BLOCKED
    assert not registry._plugins["codex"].calls
    conn.close()
