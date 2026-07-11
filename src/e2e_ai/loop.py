"""The sequential fix loop that drives tests to green.

This module re-exports the orchestrator implementation for backward
compatibility. See :mod:`e2e_ai.orchestrator` for the state-machine-driven
repair loop.
"""

from __future__ import annotations

from .orchestrator.loop import (
    FixLoop,
    LoopSummary,
    TestReport,
    TestResult,
    build_backend,
    default_reporter,
    execute_attempt,
    handle_failed_attempt,
    run_one_test_until_resolved,
    run_repair_loop,
)

__all__ = [
    "FixLoop",
    "LoopSummary",
    "TestReport",
    "TestResult",
    "build_backend",
    "default_reporter",
    "execute_attempt",
    "handle_failed_attempt",
    "run_one_test_until_resolved",
    "run_repair_loop",
]
