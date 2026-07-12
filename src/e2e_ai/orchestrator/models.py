"""State and decision models for the repair-loop orchestrator."""

from __future__ import annotations

from attrs import define, field

# Test-level repair states (see research/18).
STATE_PENDING = "pending"
STATE_RUNNING = "running"
STATE_PASSED = "passed"
STATE_FAILED = "failed"
STATE_PLANNING = "planning"
STATE_PLAN_CREATED = "plan_created"
STATE_IMPLEMENTING = "implementing"
STATE_IMPLEMENTED = "implemented"
STATE_RERUNNING = "rerunning"
STATE_INSTRUMENTING = "instrumenting"
STATE_EXTERNAL_BLOCKER = "external_blocker"
STATE_REGRESSED = "regressed"

STATE_EXHAUSTED = "exhausted_attempts"

TERMINAL_STATES = frozenset(
    {
        STATE_PASSED,
        STATE_EXTERNAL_BLOCKER,
        STATE_EXHAUSTED,
    }
)

ALL_STATES = frozenset(
    {
        STATE_PENDING,
        STATE_RUNNING,
        STATE_PASSED,
        STATE_FAILED,
        STATE_PLANNING,
        STATE_PLAN_CREATED,
        STATE_IMPLEMENTING,
        STATE_IMPLEMENTED,
        STATE_RERUNNING,
        STATE_INSTRUMENTING,
        STATE_EXTERNAL_BLOCKER,
        STATE_REGRESSED,
        STATE_EXHAUSTED,
    }
)


@define
class RepairRun:
    """One top-level e2e-ai repair run."""

    id: str = field()
    project_id: str = field()
    status: str = field(default="running")
    reason: str | None = field(default=None)
    started_at: str | None = field(default=None)
    finished_at: str | None = field(default=None)


@define
class TestRepairState:
    """Current state for one test inside a repair run."""

    test_id: str = field()
    state: str = field(default=STATE_PENDING)
    attempt_count: int = field(default=0)
    repair_round: int = field(default=0)
    has_prior_pass: bool = field(default=False)
    last_packet_id: str | None = field(default=None)
    last_plan_id: str | None = field(default=None)
    note: str = field(default="")


@define
class RepairDecision:
    """Decision after one attempt or agent invocation."""

    action: str = field()
    next_state: str = field()
    stop_run: bool = field(default=False)
    reason: str | None = field(default=None)
    plan_id: str | None = field(default=None)
    dry_run_prompt: str | None = field(default=None)
