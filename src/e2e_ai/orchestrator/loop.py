"""Repair-loop orchestration: state machine, agents, and test reruns."""

from __future__ import annotations

import logging
import os
import sqlite3
import subprocess
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from ..agents.base import AgentPlugin
from ..agents.implementation_result import parse_implementation_result
from ..agents.invocation import EXIT_QUOTA_ERROR
from ..agents.progress import LogTailFollower, format_stream_event
from ..agents.registry import AgentRegistry
from ..agents.role_agent import bind_role
from ..agents.router import ROLE_TASK_CLASS, ProviderSelection, select_provider
from ..agents.routing_outcomes import (
    RoutingAction,
    classify_invocation_exit,
    decide_routing_action,
)
from ..analysis import (
    build_failure_packet,
    build_repair_context,
    insert_failure_packet,
    trim_repair_context,
)
from ..analysis.worktree import (
    WorktreeSnapshot,
    capture_worktree_snapshot,
    diff_worktree_snapshots,
)
from ..config import EffectiveConfig
from ..inventory.models import DiscoveredTest
from ..inventory.store import list_runnable_tests
from ..isolation import (
    IsolationBackend,
    IsolationContext,
    create_isolation_backend,
)
from ..isolation.models import EnvironmentLease
from ..isolation.registry import POSTGRES_BACKENDS
from ..mcp.models import AgentMcpAttachment
from ..mcp.sessions import (
    cleanup_mcp_session,
    mcp_artifact_summary,
    prepare_agent_mcp_attachment,
)
from ..models import FailureInfo, TestStatus
from ..planner import plan_is_blocked, test_selector
from ..repair.stale_runs import (
    REASON_PROCESS_INTERRUPTED,
    mark_run_interrupted,
)
from ..repair.store import RepairStore
from ..runner import (
    TestRunRequest,
    TestRunResult,
    create_attempt_record,
    finish_attempt_record,
    new_attempt_id,
    run_attempt,
)
from ..runner.results import load_playwright_json
from ..runtime.models import RuntimeState
from ..runtime.refresh import (
    execute_runtime_refresh,
    plan_runtime_refresh,
)
from ..runtime.registry import create_target_runtime
from ..runtime.session import (
    build_runtime_context,
    managed_target_runtime,
    runtime_env_for_playwright,
)
from .decisions import (
    classify_external_blocker,
    should_escalate_to_instrumentation,
    should_stop_test,
)
from .models import (
    STATE_EXHAUSTED,
    STATE_EXTERNAL_BLOCKER,
    STATE_FAILED,
    STATE_IMPLEMENTED,
    STATE_INSTRUMENTING,
    STATE_PASSED,
    STATE_PENDING,
    STATE_PLANNING,
    STATE_REGRESSED,
    STATE_RERUNNING,
    STATE_RUNNING,
    RepairDecision,
    TestRepairState,
)
from .prompts import (
    build_implementer_prompt,
    build_instrumentation_prompt,
    build_mcp_prompt_section,
    build_planner_prompt,
)
from .state_machine import (
    EVENT_FAIL,
    EVENT_IMPLEMENT,
    EVENT_IMPLEMENTED,
    EVENT_INSTRUMENT,
    EVENT_INSTRUMENTED,
    EVENT_PASS,
    EVENT_PLAN,
    EVENT_PLAN_CREATED,
    EVENT_REGRESS,
    EVENT_RERUN,
    EVENT_START,
    next_state,
)
from .store import (
    attempt_history_counts,
    begin_agent_invocation,
    create_repair_run,
    finish_agent_invocation,
    finish_repair_run,
    format_test_history_suffix,
    has_ever_passed,
    record_repair_plan,
    set_plan_outcome,
)

logger = logging.getLogger(__name__)

Reporter = Callable[[str], None]

DEFAULT_AGENT_TIMEOUT_SECONDS = 1800


@dataclass
class FailoverTracker:
    """Per-test provider failover state for the current repair run."""

    switch_count: int = 0
    failed_by_role: dict[str, set[str]] = field(default_factory=dict)
    schema_retries: dict[str, int] = field(default_factory=dict)

    def failed_providers(self, role: str) -> set[str]:
        return self.failed_by_role.setdefault(role, set())

    def mark_failed(self, role: str, provider_id: str) -> None:
        self.failed_by_role.setdefault(role, set()).add(provider_id)
        self.switch_count += 1

    def can_switch(self, config: EffectiveConfig) -> bool:
        limit = config.routing.failover.max_switches_per_test
        if limit <= 0:
            return True
        return self.switch_count < limit

    def schema_retries_left(self, role: str, config: EffectiveConfig) -> int:
        used = self.schema_retries.get(role, 0)
        return max(0, config.routing.schema_retry_limit - used)

    def consume_schema_retry(self, role: str) -> None:
        self.schema_retries[role] = self.schema_retries.get(role, 0) + 1


@dataclass
class AgentInvocationOutcome:
    """Result of invoking a role with optional provider failover."""

    text: str
    ok: bool
    mcp_error: str | None = None
    agent_id: str | None = None
    routing_action: RoutingAction | None = None
    exit_class: str | None = None
    changed_paths: tuple[str, ...] = field(default_factory=tuple)
    runtime_refresh_actions: tuple[str, ...] = field(default_factory=tuple)


class TestResult(StrEnum):
    """Outcome of repairing one test."""

    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"


@dataclass
class TestReport:
    """Summary for one test inside a repair run."""

    test_id: str
    selector: str
    result: TestResult
    attempts: int
    note: str = ""


@dataclass
class LoopSummary:
    """Aggregate outcome for a repair run."""

    reports: list[TestReport] = field(default_factory=list)
    run_id: str | None = None
    status: str = "failed"
    reason: str | None = None
    interrupted: bool = False

    @property
    def passed(self) -> list[TestReport]:
        return [r for r in self.reports if r.result is TestResult.PASSED]

    @property
    def failed(self) -> list[TestReport]:
        return [r for r in self.reports if r.result is TestResult.FAILED]

    @property
    def blocked(self) -> list[TestReport]:
        return [r for r in self.reports if r.result is TestResult.BLOCKED]

    @property
    def all_green(self) -> bool:
        return (bool(self.reports) or self.status == "passed") and (
            not self.failed and not self.blocked
        )


def _isolation_context(
    config: EffectiveConfig,
    *,
    extra_env: Mapping[str, str] | None = None,
) -> IsolationContext:
    env = {**os.environ}
    if extra_env:
        env.update({str(key): str(value) for key, value in extra_env.items()})
    return IsolationContext(
        project_root=config.project_root,
        state_dir=config.state_dir,
        config=config,
        env=env,
    )


def _work_dir(config: EffectiveConfig, test_id: str) -> Path:
    path = config.state_dir / "work" / test_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _runs_dir(config: EffectiveConfig, test_id: str) -> Path:
    path = config.state_dir / "runs" / test_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _transition(state: TestRepairState, event: str) -> None:
    state.state = next_state(state.state, event)


def _test_report_from_state(
    test: DiscoveredTest,
    state: TestRepairState,
) -> TestReport:
    selector = test_selector(test)
    if state.state == STATE_PASSED:
        result = TestResult.PASSED
    elif state.state == STATE_EXTERNAL_BLOCKER:
        result = TestResult.BLOCKED
    else:
        result = TestResult.FAILED
    return TestReport(
        test_id=test.id,
        selector=selector,
        result=result,
        attempts=state.attempt_count,
        note=state.note,
    )


def _run_shard_coverage(
    *,
    conn: sqlite3.Connection,
    config: EffectiveConfig,
    catalog: list[DiscoveredTest],
    run_id: str,
    backend: IsolationBackend,
    plugins: Mapping[str, AgentPlugin],
    registry: AgentRegistry,
    say: Reporter,
    dry_run_agents: bool,
    runtime_env: Mapping[str, str] | None,
    runtime_state: RuntimeState | None,
    min_tests_per_slot: int,
    summary: LoopSummary,
    verbose_agents: bool = False,
) -> tuple[bool, str | None]:
    """Run tests until each fr-two slot has min pass count."""

    from ..integrations.fr_two.config import fr_two_isolation_section
    from ..integrations.fr_two.isolation import (
        build_fr_two_slots,
        pick_test_for_undercovered_slots,
        slot_for_test,
    )

    slots = build_fr_two_slots(
        fr_two_isolation_section(config),
        config.project_root,
    )
    slot_pass_counts = {slot.id: 0 for slot in slots}
    executed_ids: set[str] = set()

    while any(
        count < min_tests_per_slot for count in slot_pass_counts.values()
    ):
        needy = {
            slot_id
            for slot_id, count in slot_pass_counts.items()
            if count < min_tests_per_slot
        }
        test = pick_test_for_undercovered_slots(
            catalog,
            slots,
            needy,
            prefer_unrun=executed_ids,
        )
        if test is None:
            return True, (
                f"could not schedule passing tests for slots: {sorted(needy)}"
            )

        state = run_one_test_until_resolved(
            conn=conn,
            config=config,
            test=test,
            run_id=run_id,
            isolation=backend,
            agents=plugins,
            registry=registry,
            reporter=say,
            dry_run_agents=dry_run_agents,
            runtime_env=runtime_env,
            runtime_state=runtime_state,
            verbose_agents=verbose_agents,
        )
        executed_ids.add(test.id)
        summary.reports.append(_test_report_from_state(test, state))

        if state.state == STATE_PASSED:
            slot_id = slot_for_test(slots, test.id).id
            slot_pass_counts[slot_id] += 1
            continue
        if state.state == STATE_EXTERNAL_BLOCKER:
            return True, state.note
        return True, (
            f"shard coverage requires passing tests; failed: {test.id}"
        )

    return False, None


def execute_attempt(
    *,
    conn: sqlite3.Connection,
    config: EffectiveConfig,
    test: DiscoveredTest,
    attempt_index: int,
    run_id: str,
    isolation: IsolationBackend,
    attempt_id: str | None = None,
    runtime_env: Mapping[str, str] | None = None,
) -> tuple[TestRunResult, FailureInfo | None, EnvironmentLease]:
    """Create environment, run Playwright, and persist the result."""

    attempt_id = attempt_id or new_attempt_id(attempt_index)
    context = _isolation_context(config, extra_env=runtime_env)
    lease = isolation.create_environment(context, test, attempt_id)
    workdir = _work_dir(config, test.id)
    environment = dict(runtime_env or {})
    environment.update(lease.env)
    request = TestRunRequest(
        run_id=run_id,
        test_id=test.id,
        spec_file=test.spec_file,
        title=test.title,
        attempt_index=attempt_index,
        work_dir=workdir,
        environment=environment,
        attempt_id=attempt_id,
        project_name=test.project_name,
        line=test.line,
    )
    result, failure = run_attempt(config, request)
    create_attempt_record(
        conn,
        attempt_id=result.attempt_id,
        run_id=run_id,
        test_id=test.id,
        attempt_index=attempt_index,
        work_dir=str(result.work_dir or lease.work_dir),
        database_name=lease.database_name,
        frontend_url=lease.frontend_url,
        backend_url=lease.backend_url,
        environment_id=lease.id,
    )
    finish_attempt_record(conn, result)
    return result, failure, lease


def _build_packet(
    test: DiscoveredTest,
    result: TestRunResult,
    failure: FailureInfo | None,
    lease: EnvironmentLease | None,
):
    report = (
        load_playwright_json(result.json_report_path)
        if result.json_report_path.is_file()
        else None
    )
    failure = failure or FailureInfo(error_message="unknown failure")
    return build_failure_packet(
        test=test,
        attempt=result,
        report=report,
        lease=lease,
        failure=failure,
    )


def _working_tree_changed(project_root: Path) -> bool:
    """Return whether the git working tree has local modifications."""

    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.log(
            1,
            "could not inspect git status for noop detection: %s",
            exc,
            exc_info=True,
        )
        return True
    if result.returncode != 0:
        logger.log(
            1,
            "git status returned %d during noop detection",
            result.returncode,
        )
        return True
    return bool(result.stdout.strip())


def _implementation_refresh_actions(text: str) -> tuple[str, ...]:
    parsed = parse_implementation_result(text)
    if parsed is None:
        return ()
    return parsed.runtime_refresh_actions


def _maybe_refresh_target_runtime(
    *,
    config: EffectiveConfig,
    runtime_state: RuntimeState | None,
    run_id: str,
    changed_paths: tuple[str, ...],
    requested_actions: tuple[str, ...],
    reporter: Reporter,
) -> str | None:
    """Refresh target runtime services when configured; return error text."""

    if runtime_state is None:
        return None
    compose = config.target_runtime.docker_compose
    if compose is None or compose.refresh is None:
        return None

    refresh_plan = plan_runtime_refresh(
        compose.refresh,
        changed_paths=changed_paths,
        requested_actions=requested_actions,
    )
    if refresh_plan is None:
        return None

    reporter(
        "  [runtime] refreshing target stack: "
        + ", ".join(refresh_plan.selected_actions)
    )
    runtime = create_target_runtime(config)
    context = build_runtime_context(config, run_id, extra_env=runtime_state.env)
    execution = execute_runtime_refresh(
        runtime,
        context=context,
        state=runtime_state,
        plan=refresh_plan,
    )
    if execution.ok:
        return None
    return execution.error or "target runtime refresh failed"


def _new_agent_invocation_id() -> str:
    return f"agent_{uuid.uuid4().hex[:16]}"


def _append_mcp_prompt(
    prompt: str,
    *,
    context,
    mcp: AgentMcpAttachment | None,
) -> str:
    if mcp is not None and mcp.enabled:
        section = build_mcp_prompt_section(context=context, mcp=mcp)
        return prompt + "\n\n" + section
    if mcp is not None and mcp.degraded_reason:
        return prompt + f"\n\n(MCP unavailable: {mcp.degraded_reason})\n"
    return prompt


def _invoke_agent_once(
    registry: AgentRegistry,
    role: str,
    prompt: str,
    *,
    config: EffectiveConfig,
    run_id: str,
    test_id: str,
    conn: sqlite3.Connection,
    log_dir: Path,
    context,
    lease: EnvironmentLease | None,
    plugin_id: str,
    selection: ProviderSelection,
    switch_reason: str | None = None,
    reporter: Reporter | None = None,
    verbose_agents: bool = False,
    worktree_baseline: WorktreeSnapshot | None = None,
) -> tuple[str, bool, str | None, str, str | None]:
    agent = bind_role(
        config,
        role,
        registry._plugins,  # noqa: SLF001
        plugin_id=plugin_id,
    )
    timeout = min(
        DEFAULT_AGENT_TIMEOUT_SECONDS,
        config.repair_policy.max_agent_seconds,
    )
    invocation_id = _new_agent_invocation_id()
    mcp: AgentMcpAttachment | None = None
    plugin = registry._plugins.get(plugin_id)  # noqa: SLF001
    if lease is not None and plugin is not None:
        supports = False
        if hasattr(plugin, "supports_playwright_mcp"):
            supports = plugin.supports_playwright_mcp()
        mcp = prepare_agent_mcp_attachment(
            config=config,
            context=context,
            lease=lease,
            role=role,
            plugin_id=agent.id,
            agent_invocation_id=invocation_id,
            plugin_supports_mcp=supports,
        )
        if mcp.required and not mcp.enabled:
            return (
                "",
                False,
                "mcp_required:%s" % (mcp.degraded_reason or "setup failed"),
                agent.id,
                None,
            )
    full_prompt = _append_mcp_prompt(prompt, context=context, mcp=mcp)
    say = reporter or (lambda _msg: None)
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / f"{invocation_id}.log"
    begin_agent_invocation(
        conn,
        invocation_id=invocation_id,
        run_id=run_id,
        role=role,
        agent_id=agent.id,
        command=[agent.id, invocation_id],
        test_id=test_id,
        stdout_path=str(stdout_path),
        stderr_path=str(stdout_path),
        provider_order=list(selection.provider_order),
        switch_reason=switch_reason,
        failover_retry=selection.failover_retry,
    )

    follower: LogTailFollower | None = None
    if verbose_agents:

        def _on_progress_line(line: str) -> None:
            message = format_stream_event(agent.id, role, line)
            if message:
                say(message)

        follower = LogTailFollower(
            stdout_path,
            on_chunk=lambda _chunk: None,
            line_handler=_on_progress_line,
        )
        follower.start()
        say(f"  [agent] {role}/{agent.id} started")

    run = None
    try:
        run = agent.run(
            full_prompt,
            workdir=config.project_root,
            timeout=timeout,
            log_dir=log_dir,
            mcp=mcp if mcp is not None and mcp.enabled else None,
            invocation_id=invocation_id,
            output_path=stdout_path,
        )
    except Exception:
        logger.log(
            1,
            "agent invocation %s failed before completion",
            invocation_id,
            exc_info=True,
        )
        finish_agent_invocation(
            conn,
            invocation_id,
            status="error",
            exit_code=None,
            exit_class="task_failure",
        )
        if verbose_agents:
            say(f"  [agent] {role}/{agent.id} finished (error)")
        raise
    finally:
        if follower is not None:
            follower.stop()
    assert run is not None
    if mcp is not None and mcp.enabled and mcp.session is not None:
        summary = mcp_artifact_summary(mcp.session)
        logger.log(1, "MCP artifacts for %s: %s", invocation_id, summary)
        cleanup_mcp_session(
            mcp.session,
            keep=config.playwright_mcp.keep_artifacts_on_failure or not run.ok,
        )
    text = run.stdout.strip()
    if not text:
        text = f"(agent produced no output; exit={run.exit_code})"
    noop = False
    if role == "implementer" and run.ok:
        if worktree_baseline is not None:
            current = capture_worktree_snapshot(config.project_root)
            noop = not diff_worktree_snapshots(
                worktree_baseline,
                current,
                config.project_root,
            )
        else:
            noop = not _working_tree_changed(config.project_root)
    exit_class = classify_invocation_exit(
        run,
        role=role,
        config=config,
        plan_text=text if role in {"planner", "instrumenter"} else None,
        noop_implementation=noop,
    )
    finish_agent_invocation(
        conn,
        invocation_id,
        status="ok" if run.ok and not noop else "error",
        exit_code=run.exit_code,
        exit_class=exit_class,
    )
    if verbose_agents:
        say(f"  [agent] {role}/{agent.id} finished (exit {run.exit_code})")
    invocation_ok = run.ok and not noop
    return text, invocation_ok, None, agent.id, exit_class


def _invoke_role_with_failover(
    registry: AgentRegistry,
    role: str,
    prompt: str,
    *,
    config: EffectiveConfig,
    run_id: str,
    test_id: str,
    conn: sqlite3.Connection,
    log_dir: Path,
    context,
    lease: EnvironmentLease | None,
    failover: FailoverTracker,
    external_blocker: bool = False,
    reporter: Reporter | None = None,
    verbose_agents: bool = False,
) -> AgentInvocationOutcome:
    task_class = ROLE_TASK_CLASS.get(role, "normal")
    plugins = registry._plugins  # noqa: SLF001
    last_agent_id: str | None = None
    last_exit_class: str | None = None
    worktree_baseline = (
        capture_worktree_snapshot(config.project_root)
        if role == "implementer"
        else None
    )

    def _finalize(outcome: AgentInvocationOutcome) -> AgentInvocationOutcome:
        if role != "implementer" or worktree_baseline is None:
            return outcome
        current = capture_worktree_snapshot(config.project_root)
        changed_paths = diff_worktree_snapshots(
            worktree_baseline,
            current,
            config.project_root,
        )
        requested = _implementation_refresh_actions(outcome.text)
        return AgentInvocationOutcome(
            text=outcome.text,
            ok=outcome.ok,
            mcp_error=outcome.mcp_error,
            agent_id=outcome.agent_id,
            routing_action=outcome.routing_action,
            exit_class=outcome.exit_class,
            changed_paths=changed_paths,
            runtime_refresh_actions=requested,
        )

    while True:
        excluded = failover.failed_providers(role)
        try:
            selection = select_provider(
                config,
                role,
                task_class,
                plugins,
                excluded=excluded,
                failover_retry=bool(excluded),
            )
        except Exception as exc:
            from ..errors import AgentError

            if isinstance(exc, AgentError):
                return _finalize(
                    AgentInvocationOutcome(
                        text=str(exc),
                        ok=False,
                        agent_id=last_agent_id,
                        routing_action=RoutingAction.STOP_TEST,
                        exit_class=last_exit_class,
                    )
                )
            raise

        text, ok, mcp_err, agent_id, exit_class = _invoke_agent_once(
            registry,
            role,
            prompt,
            config=config,
            run_id=run_id,
            test_id=test_id,
            conn=conn,
            log_dir=log_dir,
            context=context,
            lease=lease,
            plugin_id=selection.selected_provider,
            selection=selection,
            switch_reason=(
                f"failover from {sorted(excluded)[-1]}" if excluded else None
            ),
            reporter=reporter,
            verbose_agents=verbose_agents,
            worktree_baseline=worktree_baseline,
        )
        last_agent_id = agent_id
        last_exit_class = exit_class

        if mcp_err:
            return _finalize(
                AgentInvocationOutcome(
                    text=text,
                    ok=False,
                    mcp_error=mcp_err,
                    agent_id=agent_id,
                    routing_action=RoutingAction.EXTERNAL_BLOCKER,
                    exit_class=exit_class,
                )
            )

        if ok:
            return _finalize(
                AgentInvocationOutcome(
                    text=text,
                    ok=True,
                    agent_id=agent_id,
                    routing_action=RoutingAction.SUCCESS,
                    exit_class=exit_class,
                )
            )

        pool = list(selection.provider_order)
        failed = failover.failed_providers(role) | {
            selection.selected_provider,
        }
        providers_remaining = any(provider not in failed for provider in pool)
        action = decide_routing_action(
            exit_class or "task_failure",
            config=config,
            external_blocker=external_blocker,
            same_provider_retries_left=failover.schema_retries_left(
                role,
                config,
            ),
            providers_remaining=providers_remaining,
            switches_remaining=failover.can_switch(config),
        )

        if action is RoutingAction.RETRY_SAME_PROVIDER:
            failover.consume_schema_retry(role)
            continue

        if action is RoutingAction.SWITCH_PROVIDER:
            if not providers_remaining or not failover.can_switch(config):
                return _finalize(
                    AgentInvocationOutcome(
                        text=text,
                        ok=False,
                        agent_id=agent_id,
                        routing_action=RoutingAction.STOP_TEST,
                        exit_class=exit_class,
                    )
                )
            failover.mark_failed(role, selection.selected_provider)
            continue

        return _finalize(
            AgentInvocationOutcome(
                text=text,
                ok=False,
                agent_id=agent_id,
                routing_action=action,
                exit_class=exit_class,
            )
        )


def _role_plugin(registry: AgentRegistry, role: str) -> AgentPlugin:
    """Return the configured plugin bound to a loop role."""

    role_id = registry.role(role).id
    return registry._plugins[role_id]  # noqa: SLF001


def _invoke_agent(
    registry: AgentRegistry,
    role: str,
    prompt: str,
    *,
    config: EffectiveConfig,
    run_id: str,
    test_id: str,
    conn: sqlite3.Connection,
    log_dir: Path,
    context,
    lease: EnvironmentLease | None,
    plugin: AgentPlugin,
    failover: FailoverTracker | None = None,
    reporter: Reporter | None = None,
    verbose_agents: bool = False,
) -> AgentInvocationOutcome:
    _ = plugin
    tracker = failover or FailoverTracker()
    return _invoke_role_with_failover(
        registry,
        role,
        prompt,
        config=config,
        run_id=run_id,
        test_id=test_id,
        conn=conn,
        log_dir=log_dir,
        context=context,
        lease=lease,
        failover=tracker,
        reporter=reporter,
        verbose_agents=verbose_agents,
    )


def handle_failed_attempt(
    *,
    conn: sqlite3.Connection,
    config: EffectiveConfig,
    test: DiscoveredTest,
    packet,
    run_id: str,
    agents: Mapping[str, AgentPlugin],
    registry: AgentRegistry,
    repair_state: TestRepairState,
    dry_run_agents: bool = False,
    lease: EnvironmentLease | None = None,
    failover: FailoverTracker | None = None,
    reporter: Reporter | None = None,
    verbose_agents: bool = False,
) -> RepairDecision:
    """Plan, implement, instrument, or stop after a failed attempt."""

    _ = agents
    tracker = failover or FailoverTracker()
    policy = config.repair_policy
    context = trim_repair_context(
        build_repair_context(conn, packet, test=test, config=config)
    )

    if classify_external_blocker(packet, config=config):
        return RepairDecision(
            action="external_blocker",
            next_state=STATE_EXTERNAL_BLOCKER,
            stop_run=True,
            reason="failure classified as external blocker",
        )

    if should_stop_test(
        conn,
        test.id,
        policy.max_attempts_per_test,
        run_id=run_id,
    ):
        return RepairDecision(
            action="exhausted",
            next_state=STATE_EXHAUSTED,
            reason="max repair attempts reached for this test",
        )

    escalate = should_escalate_to_instrumentation(
        conn,
        test.id,
        packet.signature,
        run_id=run_id,
        max_same_signature=policy.max_same_signature_attempts,
    )

    log_dir = _runs_dir(config, test.id) / "agents"

    if escalate:
        _transition(repair_state, EVENT_INSTRUMENT)
        instr_prompt = build_instrumentation_prompt(
            context,
            config=config,
            test=test,
        )
        if dry_run_agents:
            return RepairDecision(
                action="instrument_dry_run",
                next_state=STATE_INSTRUMENTING,
                dry_run_prompt=instr_prompt,
            )
        instr_outcome = _invoke_agent(
            registry,
            "instrumenter",
            instr_prompt,
            config=config,
            run_id=run_id,
            test_id=test.id,
            conn=conn,
            log_dir=log_dir,
            context=context,
            lease=lease,
            plugin=_role_plugin(registry, "instrumenter"),
            failover=tracker,
            reporter=reporter,
            verbose_agents=verbose_agents,
        )
        if instr_outcome.mcp_error:
            return RepairDecision(
                action="mcp_blocker",
                next_state=STATE_EXTERNAL_BLOCKER,
                stop_run=True,
                reason=instr_outcome.mcp_error,
            )
        _transition(repair_state, EVENT_INSTRUMENTED)
        if not instr_outcome.ok:
            repair_state.note = "instrumenter agent failed"
        context = trim_repair_context(
            build_repair_context(conn, packet, test=test, config=config)
        )
    else:
        start = repair_state.state
        if start == STATE_REGRESSED:
            _transition(repair_state, EVENT_PLAN)
        else:
            _transition(repair_state, EVENT_PLAN)

    plan_prompt = build_planner_prompt(context, config=config, test=test)
    if dry_run_agents:
        return RepairDecision(
            action="plan_dry_run",
            next_state=STATE_PLANNING,
            dry_run_prompt=plan_prompt,
        )

    plan_outcome = _invoke_agent(
        registry,
        "planner",
        plan_prompt,
        config=config,
        run_id=run_id,
        test_id=test.id,
        conn=conn,
        log_dir=log_dir,
        context=context,
        lease=lease,
        plugin=_role_plugin(registry, "planner"),
        failover=tracker,
        reporter=reporter,
        verbose_agents=verbose_agents,
    )
    plan_text = plan_outcome.text
    if plan_outcome.mcp_error:
        return RepairDecision(
            action="mcp_blocker",
            next_state=STATE_EXTERNAL_BLOCKER,
            stop_run=True,
            reason=plan_outcome.mcp_error,
        )

    if not plan_outcome.ok:
        if plan_outcome.exit_class == EXIT_QUOTA_ERROR:
            return RepairDecision(
                action="agent_quota_blocker",
                next_state=STATE_EXTERNAL_BLOCKER,
                stop_run=True,
                reason="all configured planner providers exhausted quota",
            )
        repair_state.note = "planner agent failed after provider failover"
        return RepairDecision(
            action="rerun",
            next_state=STATE_FAILED,
            reason=repair_state.note,
        )

    _transition(repair_state, EVENT_PLAN_CREATED)

    blocked = plan_is_blocked(plan_text)
    if blocked:
        return RepairDecision(
            action="planner_blocked",
            next_state=STATE_EXTERNAL_BLOCKER,
            stop_run=True,
            reason=blocked,
        )

    plan_id = record_repair_plan(
        conn,
        test_id=test.id,
        failure_packet_id=packet.id,
        agent_id=registry.role("planner").id,
        plan_text=plan_text,
    )
    repair_state.last_plan_id = plan_id

    _transition(repair_state, EVENT_IMPLEMENT)
    impl_prompt = build_implementer_prompt(
        plan_text,
        context,
        config=config,
        test=test,
    )
    impl_outcome = _invoke_agent(
        registry,
        "implementer",
        impl_prompt,
        config=config,
        run_id=run_id,
        test_id=test.id,
        conn=conn,
        log_dir=log_dir,
        context=context,
        lease=lease,
        plugin=_role_plugin(registry, "implementer"),
        failover=tracker,
        reporter=reporter,
        verbose_agents=verbose_agents,
    )
    if impl_outcome.mcp_error:
        return RepairDecision(
            action="mcp_blocker",
            next_state=STATE_EXTERNAL_BLOCKER,
            stop_run=True,
            reason=impl_outcome.mcp_error,
        )
    if not impl_outcome.ok and impl_outcome.exit_class == EXIT_QUOTA_ERROR:
        return RepairDecision(
            action="agent_quota_blocker",
            next_state=STATE_EXTERNAL_BLOCKER,
            stop_run=True,
            reason="all configured implementer providers exhausted quota",
        )
    _transition(repair_state, EVENT_IMPLEMENTED)
    set_plan_outcome(
        conn,
        plan_id,
        "implemented" if impl_outcome.ok else "implement_failed",
    )
    repair_state.repair_round += 1

    return RepairDecision(
        action="rerun",
        next_state=STATE_IMPLEMENTED,
        plan_id=plan_id,
        changed_paths=impl_outcome.changed_paths,
        runtime_refresh_actions=impl_outcome.runtime_refresh_actions,
    )


def run_one_test_until_resolved(
    *,
    conn: sqlite3.Connection,
    config: EffectiveConfig,
    test: DiscoveredTest,
    run_id: str,
    isolation: IsolationBackend,
    agents: Mapping[str, AgentPlugin],
    registry: AgentRegistry,
    reporter: Reporter | None = None,
    dry_run_agents: bool = False,
    runtime_env: Mapping[str, str] | None = None,
    runtime_state: RuntimeState | None = None,
    verbose_agents: bool = False,
) -> TestRepairState:
    """Run and repair one test until it passes or blocks."""

    say = reporter or (lambda _msg: None)
    has_prior_pass = has_ever_passed(conn, test.id)
    repair_state = TestRepairState(
        test_id=test.id,
        has_prior_pass=has_prior_pass,
    )
    selector = test_selector(test)
    run_count, failure_count = attempt_history_counts(conn, test.id)
    history_suffix = (
        format_test_history_suffix(run_count, failure_count)
        if run_count > 0
        else ""
    )
    say(f">> {selector}{history_suffix}")

    attempt_budget = max(1, config.repair_policy.max_attempts_per_test)
    attempt_index = 0
    lease = None
    failover = FailoverTracker()

    for attempt in range(attempt_budget):
        if repair_state.state == STATE_PENDING:
            _transition(repair_state, EVENT_START)

        result, failure, lease = execute_attempt(
            conn=conn,
            config=config,
            test=test,
            attempt_index=attempt_index,
            run_id=run_id,
            isolation=isolation,
            runtime_env=runtime_env,
        )
        repair_state.attempt_count += 1
        attempt_index += 1

        if result.passed:
            if repair_state.state == STATE_RERUNNING:
                _transition(repair_state, EVENT_PASS)
            else:
                repair_state.state = STATE_PASSED
            conn.execute(
                "UPDATE tests SET last_status = ? WHERE id = ?",
                (TestStatus.PASSING.value, test.id),
            )
            conn.commit()
            if lease is not None:
                isolation.cleanup_environment(lease, "passed")
            say(f"  [pass] passed on attempt {repair_state.attempt_count}")
            return repair_state

        # Failed run.
        if (
            has_prior_pass
            and repair_state.state == STATE_RUNNING
            and attempt == 0
        ):
            _transition(repair_state, EVENT_REGRESS)
        elif repair_state.state == STATE_RERUNNING:
            _transition(repair_state, EVENT_FAIL)
        elif repair_state.state == STATE_RUNNING:
            _transition(repair_state, EVENT_FAIL)

        failure = failure or FailureInfo(error_message="unknown failure")

        if failure.is_environmental():
            repair_state.state = STATE_EXTERNAL_BLOCKER
            repair_state.note = (
                "failure looks environmental (setup/services), not code"
            )
            conn.execute(
                "UPDATE tests SET last_status = ? WHERE id = ?",
                (TestStatus.BLOCKED.value, test.id),
            )
            conn.commit()
            if lease is not None:
                isolation.cleanup_environment(lease, "blocked")
            say(f"  [blocked] {repair_state.note}")
            return repair_state

        packet = _build_packet(test, result, failure, lease)
        insert_failure_packet(conn, packet)
        repair_state.last_packet_id = packet.id
        conn.execute(
            "UPDATE tests SET last_status = ? WHERE id = ?",
            (TestStatus.FAILING.value, test.id),
        )
        conn.commit()

        first_line = (
            failure.error_message.splitlines()[0][:120]
            if failure.error_message
            else "no message"
        )
        say(
            f"  [fail] failed (attempt {repair_state.attempt_count}/"
            f"{attempt_budget}): {first_line}"
        )

        if attempt >= attempt_budget - 1:
            break

        decision = handle_failed_attempt(
            conn=conn,
            config=config,
            test=test,
            packet=packet,
            run_id=run_id,
            agents=agents,
            registry=registry,
            repair_state=repair_state,
            dry_run_agents=dry_run_agents,
            lease=lease,
            failover=failover,
            reporter=say,
            verbose_agents=verbose_agents,
        )

        if decision.dry_run_prompt:
            say("  ~ dry-run: would invoke agents")
            if lease is not None:
                isolation.cleanup_environment(lease, "failed")
            repair_state.note = "dry-run-agents"
            repair_state.state = STATE_FAILED
            return repair_state

        if decision.stop_run:
            repair_state.state = decision.next_state
            if decision.next_state == STATE_EXTERNAL_BLOCKER:
                conn.execute(
                    "UPDATE tests SET last_status = ? WHERE id = ?",
                    (TestStatus.BLOCKED.value, test.id),
                )
            conn.commit()
            repair_state.note = decision.reason or ""
            if lease is not None:
                isolation.cleanup_environment(lease, "blocked")
            say("  [blocked] %s" % (decision.reason or "stopped"))
            return repair_state

        if decision.action == "exhausted":
            repair_state.state = STATE_EXHAUSTED
            repair_state.note = decision.reason or "attempts exhausted"
            if lease is not None:
                isolation.cleanup_environment(lease, "failed")
            say(f"  [fail] {repair_state.note}")
            return repair_state

        # Rerun after implement; otherwise apply the decision state
        # (e.g. planner failed while still in planning).
        if repair_state.state == STATE_IMPLEMENTED:
            refresh_error = _maybe_refresh_target_runtime(
                config=config,
                runtime_state=runtime_state,
                run_id=run_id,
                changed_paths=decision.changed_paths,
                requested_actions=decision.runtime_refresh_actions,
                reporter=say,
            )
            if refresh_error:
                repair_state.state = STATE_EXTERNAL_BLOCKER
                repair_state.note = refresh_error
                conn.execute(
                    "UPDATE tests SET last_status = ? WHERE id = ?",
                    (TestStatus.BLOCKED.value, test.id),
                )
                conn.commit()
                if lease is not None:
                    isolation.cleanup_environment(lease, "blocked")
                say(f"  [blocked] {refresh_error}")
                return repair_state
            _transition(repair_state, EVENT_RERUN)
        else:
            repair_state.state = decision.next_state

    repair_state.state = STATE_EXHAUSTED
    repair_state.note = "attempts exhausted"
    if lease is not None:
        isolation.cleanup_environment(lease, "failed")
    say("  [fail] giving up after exhausting attempts")
    return repair_state


def _will_start_docker_containers(
    config: EffectiveConfig,
    *,
    start_runtime: bool,
) -> bool:
    """Return whether the repair loop will bring up Docker before tests run."""

    if start_runtime and config.target_runtime.backend == "docker_compose":
        return True
    return config.isolation.backend in POSTGRES_BACKENDS


def run_repair_loop(
    config: EffectiveConfig,
    conn: sqlite3.Connection,
    registry: AgentRegistry,
    *,
    isolation: IsolationBackend | None = None,
    reporter: Reporter | None = None,
    limit: int | None = None,
    test_ids: list[str] | None = None,
    only_failed: bool = False,
    dry_run_agents: bool = False,
    stop_on_failure: bool = False,
    start_runtime: bool = True,
    min_tests_per_slot: int | None = None,
    verbose_agents: bool = False,
) -> LoopSummary:
    """Run tests until green or blocked."""

    backend = isolation or create_isolation_backend(config)
    plugins = registry._plugins  # noqa: SLF001 — orchestrator needs plugin map
    say = reporter or (lambda _msg: None)

    for path in (config.state_dir / "work", config.state_dir / "runs"):
        path.mkdir(parents=True, exist_ok=True)

    pending = list_runnable_tests(conn, config.project_id)
    catalog = list(pending)
    previous_run_id: str | None = None
    if only_failed:
        repair_store = RepairStore(conn)
        previous_run_id = repair_store.latest_finished_run_id(
            config.project_id,
        )
        if previous_run_id is None:
            say("No previous finished run with attempts; nothing to repair.")
            summary = LoopSummary(status="passed", reason="no previous run")
            return summary
        failed_ids = repair_store.test_ids_not_passed_in_run(previous_run_id)
        pending = [t for t in pending if t.id in failed_ids]
        catalog = list(pending)
    if test_ids is not None:
        wanted = set(test_ids)
        pending = [t for t in pending if t.id in wanted]
        catalog = list(pending)
    if limit is not None and min_tests_per_slot is None:
        pending = pending[:limit]

    summary = LoopSummary()
    if only_failed and not pending:
        say(f"Scheduling 0 test(s) that failed in run {previous_run_id}.")
        summary.status = "passed"
        summary.reason = "no failed tests"
        return summary

    run_id = create_repair_run(conn, config)
    summary.run_id = run_id
    stopped = False
    interrupted = False
    stop_reason: str | None = None

    if min_tests_per_slot is not None:
        say(
            f"Scheduling shard coverage: at least {min_tests_per_slot} "
            f"passing test(s) per slot."
        )
    elif only_failed:
        say(
            f"Scheduling {len(pending)} test(s) that failed in run "
            f"{previous_run_id}."
        )
    else:
        say(f"Scheduling {len(pending)} test(s).")
    if _will_start_docker_containers(config, start_runtime=start_runtime):
        say("Starting Docker containers...")
    context = _isolation_context(config)

    def _runtime_outcome() -> str:
        return "passed" if summary.all_green else "failed"

    try:
        with managed_target_runtime(
            config,
            run_id,
            enabled=start_runtime,
            outcome_fn=_runtime_outcome,
        ) as runtime_state:
            runtime_env = runtime_env_for_playwright(runtime_state)
            context = _isolation_context(config, extra_env=runtime_env)
            backend.prepare_baseline(context)
            if min_tests_per_slot is not None:
                stopped, stop_reason = _run_shard_coverage(
                    conn=conn,
                    config=config,
                    catalog=catalog,
                    run_id=run_id,
                    backend=backend,
                    plugins=plugins,
                    registry=registry,
                    say=say,
                    dry_run_agents=dry_run_agents,
                    runtime_env=runtime_env,
                    runtime_state=runtime_state,
                    min_tests_per_slot=min_tests_per_slot,
                    summary=summary,
                    verbose_agents=verbose_agents,
                )
            else:
                for test in pending:
                    state = run_one_test_until_resolved(
                        conn=conn,
                        config=config,
                        test=test,
                        run_id=run_id,
                        isolation=backend,
                        agents=plugins,
                        registry=registry,
                        reporter=say,
                        dry_run_agents=dry_run_agents,
                        runtime_env=runtime_env,
                        runtime_state=runtime_state,
                        verbose_agents=verbose_agents,
                    )
                    summary.reports.append(
                        _test_report_from_state(test, state),
                    )

                    if state.state == STATE_EXTERNAL_BLOCKER:
                        if config.repair_policy.stop_on_first_unsolvable:
                            stopped = True
                            stop_reason = state.note
                            break
                    if stop_on_failure and state.state != STATE_PASSED:
                        stopped = True
                        stop_reason = "stop on failure"
                        break
    except KeyboardInterrupt:
        interrupted = True
        stopped = True
        stop_reason = REASON_PROCESS_INTERRUPTED
        say("Interrupted.")
    finally:
        if interrupted:
            mark_run_interrupted(conn, run_id)
            status = "stopped"
        elif stopped:
            status = "stopped"
            finish_repair_run(conn, run_id, status, stop_reason)
        elif summary.all_green:
            status = "passed"
            finish_repair_run(conn, run_id, status, stop_reason)
        else:
            status = "failed"
            finish_repair_run(conn, run_id, status, stop_reason)
        summary.status = status
        summary.reason = stop_reason
        summary.interrupted = interrupted

    return summary


# Backward-compatible FixLoop wrapper used by CLI and older tests.
class FixLoop:
    """Sequential fix loop delegating to :func:`run_repair_loop`."""

    def __init__(
        self,
        config: EffectiveConfig,
        conn: sqlite3.Connection,
        registry: AgentRegistry,
        *,
        backend: IsolationBackend | None = None,
        reporter: Reporter | None = None,
        dry_run: bool = False,
        verbose_agents: bool = False,
    ) -> None:
        self.config = config
        self.conn = conn
        self.registry = registry
        self.backend = backend or create_isolation_backend(config)
        self.dry_run = dry_run
        self.verbose_agents = verbose_agents
        self._say = reporter or (lambda msg: None)
        self.store = _LegacyStore(conn)

    def ensure_dirs(self) -> None:
        for path in (
            self.config.state_dir / "work",
            self.config.state_dir / "runs",
        ):
            path.mkdir(parents=True, exist_ok=True)

    def fix_one_test(self, test: DiscoveredTest, *, run_id: str) -> TestReport:
        plugins = self.registry._plugins  # noqa: SLF001
        state = run_one_test_until_resolved(
            conn=self.conn,
            config=self.config,
            test=test,
            run_id=run_id,
            isolation=self.backend,
            agents=plugins,
            registry=self.registry,
            reporter=self._say,
            dry_run_agents=self.dry_run,
            verbose_agents=self.verbose_agents,
        )
        return _test_report_from_state(test, state)

    def run(
        self,
        *,
        limit: int | None = None,
        test_ids: list[str] | None = None,
        only_failed: bool = False,
        stop_on_failure: bool = False,
        start_runtime: bool = True,
        min_tests_per_slot: int | None = None,
    ) -> LoopSummary:
        return run_repair_loop(
            self.config,
            self.conn,
            self.registry,
            isolation=self.backend,
            reporter=self._say,
            limit=limit,
            test_ids=test_ids,
            only_failed=only_failed,
            dry_run_agents=self.dry_run,
            stop_on_failure=stop_on_failure,
            start_runtime=start_runtime,
            min_tests_per_slot=min_tests_per_slot,
            verbose_agents=self.verbose_agents,
        )


class _LegacyStore:
    """Minimal adapter so ``loop.store`` keeps working in tests."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        from ..repair.store import RepairStore

        self._inner = RepairStore(conn)

    def start_run(self, project_id: str, *, reason: str | None = None) -> str:
        return self._inner.start_run(project_id, reason=reason)

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        reason: str | None = None,
    ):
        return self._inner.finish_run(run_id, status=status, reason=reason)

    def has_ever_passed(self, test_id: str) -> bool:
        return self._inner.has_ever_passed(test_id)

    def previous_plans(self, test_id: str, *, limit: int = 10):
        return self._inner.previous_plans(test_id, limit=limit)

    def previous_failures(self, test_id: str, *, limit: int = 10):
        return self._inner.previous_failures(test_id, limit=limit)

    def record_plan(self, **kwargs):
        return self._inner.record_plan(**kwargs)

    def set_plan_outcome(self, plan_id: str, outcome: str) -> None:
        return self._inner.set_plan_outcome(plan_id, outcome)

    def record_agent_invocation(self, **kwargs):
        return self._inner.record_agent_invocation(**kwargs)

    def set_test_status(self, test_id: str, status: TestStatus) -> None:
        return self._inner.set_test_status(test_id, status)


def build_backend(config: EffectiveConfig) -> IsolationBackend:
    """Return the configured isolation backend."""

    return create_isolation_backend(config)


def default_reporter(msg: str) -> None:  # pragma: no cover
    print(msg, flush=True)
