"""Low-level subprocess helper that streams output to log files.

The helper never invokes a shell — argv is passed as a list — so test titles and
paths with spaces or metacharacters are safe.
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

# Sentinel exit codes distinguishing why a process did not finish normally.
TIMEOUT_EXIT_CODE = 124
RUNNER_ERROR_EXIT_CODE = 127


def run_command_to_logs(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    stdout_path: Path,
    stderr_path: Path,
    timeout_seconds: int,
) -> int:
    """Run a process and write stdout and stderr to log files.

    When ``stdout_path`` and ``stderr_path`` are the same file, stderr is merged
    into stdout so the caller gets one combined log (the project's preferred
    layout). Returns the process exit code, or :data:`TIMEOUT_EXIT_CODE` on
    timeout and :data:`RUNNER_ERROR_EXIT_CODE` when the process cannot launch.
    """

    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    combined = stdout_path == stderr_path

    stdout_handle = stdout_path.open("wb")
    stderr_handle = None
    try:
        if combined:
            stderr_target: int | object = subprocess.STDOUT
        else:
            stderr_handle = stderr_path.open("wb")
            stderr_target = stderr_handle
        try:
            process = subprocess.Popen(
                list(argv),
                cwd=str(cwd),
                env=dict(env),
                stdout=stdout_handle,
                stderr=stderr_target,
            )
        except (OSError, ValueError):
            stdout_handle.write(b"[e2e-ai] failed to launch process\n")
            return RUNNER_ERROR_EXIT_CODE
        try:
            return process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            return TIMEOUT_EXIT_CODE
    finally:
        stdout_handle.close()
        if stderr_handle is not None:
            stderr_handle.close()
