"""Map agent invocation outcomes to repair-loop routing decisions."""

from __future__ import annotations

from enum import StrEnum

from ..config.models import EffectiveConfig
from .base import AgentRunResult
from .invocation import (
    EXIT_AUTH_ERROR,
    EXIT_MAX_TURNS_EXCEEDED,
    EXIT_MISCONFIGURED,
    EXIT_MODEL_UNAVAILABLE,
    EXIT_PERMISSION_DENIED,
    EXIT_QUOTA_ERROR,
    EXIT_SCHEMA_FAILURE,
    EXIT_TASK_FAILURE,
    EXIT_TIMEOUT,
    EXIT_TRANSIENT_CAPACITY,
    classify_agent_exit,
)

EXIT_EMPTY_OUTPUT = "empty_output"
EXIT_NOOP_IMPLEMENTATION = "noop_implementation"

DEFAULT_RETRYABLE_EXIT_CLASSES: tuple[str, ...] = (
    EXIT_AUTH_ERROR,
    EXIT_QUOTA_ERROR,
    EXIT_TRANSIENT_CAPACITY,
    EXIT_TIMEOUT,
    EXIT_SCHEMA_FAILURE,
    EXIT_MODEL_UNAVAILABLE,
    EXIT_EMPTY_OUTPUT,
    EXIT_NOOP_IMPLEMENTATION,
    EXIT_MAX_TURNS_EXCEEDED,
    EXIT_PERMISSION_DENIED,
)


class RoutingAction(StrEnum):
    """Routing decision after an agent invocation."""

    SUCCESS = "success"
    RETRY_SAME_PROVIDER = "retry_same_provider"
    SWITCH_PROVIDER = "switch_provider"
    STOP_TEST = "stop_test"
    EXTERNAL_BLOCKER = "external_blocker"


def retryable_exit_classes(config: EffectiveConfig) -> frozenset[str]:
    """Return configured retryable exit classes."""

    configured = config.routing.failover.retryable_exit_classes
    if configured:
        return frozenset(configured)
    return frozenset(DEFAULT_RETRYABLE_EXIT_CLASSES)


def classify_invocation_exit(
    run: AgentRunResult,
    *,
    role: str,
    config: EffectiveConfig,
    plan_text: str | None = None,
    noop_implementation: bool = False,
) -> str:
    """Return the exit class used for failover routing."""

    if noop_implementation:
        return EXIT_NOOP_IMPLEMENTATION
    require_schema = config.routing.planner_requires_schema and role in {
        "planner",
        "instrumenter",
    }
    if require_schema and plan_text is not None:
        stripped = plan_text.strip()
        if not stripped or stripped.startswith("(agent produced no output"):
            return EXIT_EMPTY_OUTPUT
    if run.ok:
        return None
    derived = classify_agent_exit(run.exit_code, run.stdout, run.stderr)
    if run.exit_class and run.exit_class not in {None, EXIT_TASK_FAILURE}:
        return run.exit_class
    if derived != EXIT_TASK_FAILURE:
        return derived
    if run.exit_class:
        return run.exit_class
    if not run.stdout.strip():
        return EXIT_EMPTY_OUTPUT
    return EXIT_TASK_FAILURE


def decide_routing_action(
    exit_class: str,
    *,
    config: EffectiveConfig,
    external_blocker: bool = False,
    same_provider_retries_left: int = 0,
    providers_remaining: bool = True,
    switches_remaining: bool = True,
) -> RoutingAction:
    """Decide whether to retry, switch provider, or stop."""

    if external_blocker:
        return RoutingAction.EXTERNAL_BLOCKER

    failover = config.routing.failover
    if not failover.enabled:
        if exit_class in {
            EXIT_TASK_FAILURE,
            EXIT_PERMISSION_DENIED,
            EXIT_MISCONFIGURED,
        }:
            return RoutingAction.STOP_TEST
        return RoutingAction.STOP_TEST

    retryable = retryable_exit_classes(config)
    if exit_class not in retryable:
        return RoutingAction.STOP_TEST

    if exit_class == EXIT_SCHEMA_FAILURE and same_provider_retries_left > 0:
        return RoutingAction.RETRY_SAME_PROVIDER

    if not providers_remaining or not switches_remaining:
        return RoutingAction.STOP_TEST

    return RoutingAction.SWITCH_PROVIDER
