"""Playwright execution primitive: run one test attempt and capture artifacts.

This package provides a reliable, agent-free execution primitive the repair
loop and the ``e2e-ai run`` command build on. See
`research/14. Playwright Execution and Artifacts.md`.
"""

from __future__ import annotations

from .artifacts import (
    collect_playwright_artifacts,
    write_command_manifest,
    write_environment_manifest,
)
from .commands import build_playwright_test_command, build_spec_command
from .models import (
    ATTEMPT_STATUSES,
    STATUS_FAILED,
    STATUS_INTERRUPTED,
    STATUS_PASSED,
    STATUS_RUNNER_ERROR,
    STATUS_TIMED_OUT,
    TestRunRequest,
    TestRunResult,
)
from .playwright import (
    build_playwright_env,
    new_attempt_id,
    run_attempt,
    run_playwright_test,
)
from .results import (
    attempt_status_from_report,
    extract_failure,
    load_playwright_json,
    summarize_playwright_json,
)
from .store import create_attempt_record, finish_attempt_record

__all__ = [
    "ATTEMPT_STATUSES",
    "STATUS_FAILED",
    "STATUS_INTERRUPTED",
    "STATUS_PASSED",
    "STATUS_RUNNER_ERROR",
    "STATUS_TIMED_OUT",
    "TestRunRequest",
    "TestRunResult",
    "attempt_status_from_report",
    "build_playwright_env",
    "build_playwright_test_command",
    "build_spec_command",
    "collect_playwright_artifacts",
    "create_attempt_record",
    "extract_failure",
    "finish_attempt_record",
    "load_playwright_json",
    "new_attempt_id",
    "run_attempt",
    "run_playwright_test",
    "summarize_playwright_json",
    "write_command_manifest",
    "write_environment_manifest",
]
