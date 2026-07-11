"""Value objects for one Playwright test attempt."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from attrs import define, field

# Normalized attempt status values.
STATUS_PASSED = "passed"
STATUS_FAILED = "failed"
STATUS_TIMED_OUT = "timed_out"
STATUS_INTERRUPTED = "interrupted"
STATUS_RUNNER_ERROR = "runner_error"

ATTEMPT_STATUSES = frozenset(
    {
        STATUS_PASSED,
        STATUS_FAILED,
        STATUS_TIMED_OUT,
        STATUS_INTERRUPTED,
        STATUS_RUNNER_ERROR,
    }
)


@define
class TestRunRequest:
    """Request to run one test attempt."""

    run_id: str = field()
    test_id: str = field()
    spec_file: str = field()
    title: str = field()
    attempt_index: int = field()
    work_dir: Path = field()
    environment: Mapping[str, str] = field(factory=dict)
    attempt_id: str | None = field(default=None)
    # Optional Playwright project name used to isolate a single browser project.
    project_name: str | None = field(default=None)
    # Optional exact line for a precise ``file:line`` selector (unused by the
    # default ``-g`` command but kept for callers that want it).
    line: int | None = field(default=None)


@define
class TestRunResult:
    """Result of one Playwright test attempt."""

    attempt_id: str = field()
    test_id: str = field()
    status: str = field()
    exit_code: int = field()
    duration_seconds: float = field()
    stdout_path: Path = field()
    stderr_path: Path = field()
    json_report_path: Path = field()
    blob_report_path: Path | None = field(default=None)
    work_dir: Path | None = field(default=None)
    attempt_index: int = field(default=0)
    database_name: str | None = field(default=None)

    @property
    def passed(self) -> bool:
        return self.status == STATUS_PASSED
