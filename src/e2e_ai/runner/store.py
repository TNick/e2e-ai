"""Persist attempt rows produced by the Playwright runner."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from .models import TestRunResult
from .playwright import new_attempt_id


def _now() -> str:
    return datetime.now(tz=UTC).isoformat()


def create_attempt_record(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    test_id: str,
    attempt_index: int,
    work_dir: str,
    attempt_id: str | None = None,
    database_name: str | None = None,
    frontend_url: str | None = None,
    backend_url: str | None = None,
    environment_id: str | None = None,
) -> str:
    """Insert an attempt row and return the attempt id."""

    attempt_id = attempt_id or new_attempt_id(attempt_index)
    conn.execute(
        """
        INSERT INTO attempts (
            id, run_id, test_id, attempt_index, status, work_dir,
            environment_id, database_name, frontend_url, backend_url, started_at
        ) VALUES (?, ?, ?, ?, 'running', ?, ?, ?, ?, ?, ?)
        """,
        (
            attempt_id,
            run_id,
            test_id,
            attempt_index,
            work_dir,
            environment_id,
            database_name,
            frontend_url,
            backend_url,
            _now(),
        ),
    )
    conn.commit()
    return attempt_id


def finish_attempt_record(
    conn: sqlite3.Connection,
    result: TestRunResult,
) -> None:
    """Update the attempt row after Playwright exits."""

    conn.execute(
        "UPDATE attempts SET finished_at = ?, status = ?, exit_code = ? WHERE id = ?",
        (_now(), result.status, result.exit_code, result.attempt_id),
    )
    conn.commit()
