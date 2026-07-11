"""Run one Playwright test attempt and capture its artifacts."""

from __future__ import annotations

import os
import secrets
import time
from pathlib import Path

from ..config import EffectiveConfig
from ..errors import CatalogError
from ..inventory.models import DiscoveredTest
from ..models import FailureInfo
from .artifacts import write_command_manifest, write_environment_manifest
from .commands import build_playwright_test_command
from .models import (
    STATUS_RUNNER_ERROR,
    STATUS_TIMED_OUT,
    TestRunRequest,
    TestRunResult,
)
from .results import attempt_status_from_report, extract_failure, load_playwright_json
from .subprocess import (
    RUNNER_ERROR_EXIT_CODE,
    TIMEOUT_EXIT_CODE,
    run_command_to_logs,
)

# No per-test timeout lives in config; this bounds a hung browser so the loop
# cannot stall indefinitely.
DEFAULT_TEST_TIMEOUT_SECONDS = 900


def new_attempt_id(attempt_index: int) -> str:
    """Return a work-directory-safe id combining the index and a random suffix."""

    return f"{attempt_index:03d}-{secrets.token_hex(3)}"


def build_playwright_env(
    config: EffectiveConfig,
    request: TestRunRequest,
    json_report_path: Path,
    blob_report_path: Path | None,
) -> dict[str, str]:
    """Build environment variables for a Playwright attempt.

    Starts from the current environment, layers on values supplied by the
    isolation backend (via ``request.environment`` — database name, service
    URLs, project flags), and forces the configured report output paths so JSON
    (and optional blob) reports never collide across attempts.
    """

    env: dict[str, str] = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    env.update({str(k): str(v) for k, v in request.environment.items()})
    env[config.playwright.report_env.json] = str(json_report_path)
    if blob_report_path is not None:
        env[config.playwright.report_env.blob] = str(blob_report_path)
    return env


def run_playwright_test(
    config: EffectiveConfig,
    request: TestRunRequest,
    *,
    timeout_seconds: int = DEFAULT_TEST_TIMEOUT_SECONDS,
) -> TestRunResult:
    """Run one Playwright test and capture artifacts.

    ``request.work_dir`` is the per-test directory; this creates a per-attempt
    subdirectory ``<work_dir>/<attempt-id>/`` holding the combined log, the JSON
    report, and command/environment manifests.
    """

    test_dir = config.project_root / config.playwright.cwd
    if not test_dir.is_dir():
        raise CatalogError(f"playwright cwd does not exist: {test_dir}")

    attempt_id = request.attempt_id or new_attempt_id(request.attempt_index)
    attempt_dir = request.work_dir / attempt_id
    attempt_dir.mkdir(parents=True, exist_ok=True)

    json_report_path = attempt_dir / "playwright-results.json"
    blob_report_path = attempt_dir / "blob-report.zip"
    log_path = attempt_dir / "output.log"  # combined stdout+stderr

    test = DiscoveredTest(
        id=request.test_id,
        title=request.title,
        spec_file=request.spec_file,
        project_name=request.project_name,
        line=request.line,
    )
    argv = build_playwright_test_command(config, test)
    env = build_playwright_env(config, request, json_report_path, blob_report_path)

    write_command_manifest(attempt_dir, argv, test_dir, list(env.keys()))
    write_environment_manifest(attempt_dir, dict(request.environment))

    started = time.monotonic()
    exit_code = run_command_to_logs(
        argv,
        cwd=test_dir,
        env=env,
        stdout_path=log_path,
        stderr_path=log_path,
        timeout_seconds=timeout_seconds,
    )
    duration = time.monotonic() - started

    data = load_playwright_json(json_report_path) if json_report_path.is_file() else {}
    if exit_code == TIMEOUT_EXIT_CODE:
        status = STATUS_TIMED_OUT
    elif exit_code == RUNNER_ERROR_EXIT_CODE and not data:
        status = STATUS_RUNNER_ERROR
    else:
        status = attempt_status_from_report(exit_code, data or None)

    return TestRunResult(
        attempt_id=attempt_id,
        test_id=request.test_id,
        status=status,
        exit_code=exit_code,
        duration_seconds=duration,
        stdout_path=log_path,
        stderr_path=log_path,
        json_report_path=json_report_path,
        blob_report_path=(blob_report_path if blob_report_path.is_file() else None),
        work_dir=attempt_dir,
        attempt_index=request.attempt_index,
        database_name=request.environment.get("E2E_AI_DATABASE"),
    )


def run_attempt(
    config: EffectiveConfig,
    request: TestRunRequest,
    *,
    timeout_seconds: int = DEFAULT_TEST_TIMEOUT_SECONDS,
) -> tuple[TestRunResult, FailureInfo | None]:
    """Run one attempt and, on failure, extract structured failure context.

    This is the seam the repair loop calls: it wraps the pure
    :func:`run_playwright_test` primitive with report/log-based failure
    extraction so the caller does not have to re-parse artifacts.
    """

    result = run_playwright_test(config, request, timeout_seconds=timeout_seconds)
    if result.passed:
        return result, None

    data = (
        load_playwright_json(result.json_report_path)
        if result.json_report_path.is_file()
        else {}
    )
    log_text = ""
    if result.stdout_path.is_file():
        log_text = result.stdout_path.read_text(encoding="utf-8", errors="replace")

    failure = extract_failure(data or None, log_text)
    if result.status == STATUS_TIMED_OUT:
        failure.error_message = (
            f"test timed out after {timeout_seconds}s: {failure.error_message}"
        )
    elif result.status == STATUS_RUNNER_ERROR and not failure.stderr_tail:
        failure.stderr_tail = log_text[-4000:]
    return result, failure
