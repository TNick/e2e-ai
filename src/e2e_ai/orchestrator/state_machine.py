"""Valid state transitions for per-test repair orchestration."""

from __future__ import annotations

from .models import (
    STATE_EXHAUSTED,
    STATE_EXTERNAL_BLOCKER,
    STATE_FAILED,
    STATE_IMPLEMENTED,
    STATE_IMPLEMENTING,
    STATE_INSTRUMENTING,
    STATE_PASSED,
    STATE_PENDING,
    STATE_PLAN_CREATED,
    STATE_PLANNING,
    STATE_REGRESSED,
    STATE_RERUNNING,
    STATE_RUNNING,
)

# Events that drive transitions.
EVENT_START = "start"
EVENT_PASS = "pass"
EVENT_FAIL = "fail"
EVENT_REGRESS = "regress"
EVENT_PLAN = "plan"
EVENT_PLAN_CREATED = "plan_created"
EVENT_IMPLEMENT = "implement"
EVENT_IMPLEMENTED = "implemented"
EVENT_RERUN = "rerun"
EVENT_INSTRUMENT = "instrument"
EVENT_INSTRUMENTED = "instrumented"
EVENT_EXTERNAL_BLOCKER = "external_blocker"
EVENT_EXHAUSTED = "exhausted"

_TRANSITIONS: dict[tuple[str, str], str] = {
    (STATE_PENDING, EVENT_START): STATE_RUNNING,
    (STATE_RUNNING, EVENT_PASS): STATE_PASSED,
    (STATE_RUNNING, EVENT_FAIL): STATE_FAILED,
    (STATE_RUNNING, EVENT_REGRESS): STATE_REGRESSED,
    (STATE_REGRESSED, EVENT_PLAN): STATE_PLANNING,
    (STATE_FAILED, EVENT_PLAN): STATE_PLANNING,
    (STATE_PLANNING, EVENT_PLAN_CREATED): STATE_PLAN_CREATED,
    (STATE_PLAN_CREATED, EVENT_IMPLEMENT): STATE_IMPLEMENTING,
    (STATE_IMPLEMENTING, EVENT_IMPLEMENTED): STATE_IMPLEMENTED,
    (STATE_IMPLEMENTED, EVENT_RERUN): STATE_RERUNNING,
    (STATE_RERUNNING, EVENT_PASS): STATE_PASSED,
    (STATE_RERUNNING, EVENT_FAIL): STATE_FAILED,
    (STATE_REGRESSED, EVENT_INSTRUMENT): STATE_INSTRUMENTING,
    (STATE_FAILED, EVENT_INSTRUMENT): STATE_INSTRUMENTING,
    (STATE_INSTRUMENTING, EVENT_INSTRUMENTED): STATE_PLANNING,
    (STATE_FAILED, EVENT_EXTERNAL_BLOCKER): STATE_EXTERNAL_BLOCKER,
    (STATE_FAILED, EVENT_EXHAUSTED): STATE_EXHAUSTED,
    (STATE_PASSED, EVENT_REGRESS): STATE_REGRESSED,
}


class InvalidTransitionError(ValueError):
    """Raised when a state/event pair is not allowed."""


def validate_transition(current: str, event: str) -> None:
    """Raise when a state transition is invalid."""

    if (current, event) not in _TRANSITIONS:
        raise InvalidTransitionError(
            f"invalid transition: state={current!r} event={event!r}"
        )


def next_state(current: str, event: str) -> str:
    """Return the next test repair state."""

    validate_transition(current, event)
    return _TRANSITIONS[(current, event)]
