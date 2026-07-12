"""Reconcile repair runs left in ``running`` after abrupt process exit."""

from __future__ import annotations

import ctypes
import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from ..runner.models import STATUS_INTERRUPTED

logger = logging.getLogger(__name__)

REASON_PROCESS_INTERRUPTED = "process interrupted"
RUN_STATUS_STOPPED = "stopped"
AGENT_STATUS_INTERRUPTED = "interrupted"


def _utc_now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def process_exists(pid: int | None) -> bool:
    """Return whether ``pid`` refers to a live process on this host."""

    if pid is None or pid <= 0:
        return False
    if os.name == "nt":
        synchronize = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(
            synchronize,
            False,
            int(pid),
        )
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(int(pid), 0)
    except OSError:
        return False
    return True


@dataclass(frozen=True)
class StaleRunReconciliation:
    """Outcome of reconciling orphaned ``running`` runs."""

    stopped_run_ids: tuple[str, ...]


def _finish_open_attempts(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    finished_at: str,
) -> None:
    conn.execute(
        """
        UPDATE attempts
        SET finished_at = ?, status = ?
        WHERE run_id = ? AND finished_at IS NULL
        """,
        (finished_at, STATUS_INTERRUPTED, run_id),
    )
    conn.execute(
        """
        UPDATE agent_invocations
        SET finished_at = ?, status = ?
        WHERE run_id = ? AND finished_at IS NULL
        """,
        (finished_at, AGENT_STATUS_INTERRUPTED, run_id),
    )


def mark_run_interrupted(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    reason: str = REASON_PROCESS_INTERRUPTED,
) -> None:
    """Finish a ``running`` run and any open attempts after abrupt exit."""

    finished_at = _utc_now_iso()
    conn.execute(
        """
        UPDATE runs
        SET finished_at = ?, status = ?, reason = ?
        WHERE id = ?
        """,
        (finished_at, RUN_STATUS_STOPPED, reason, run_id),
    )
    _finish_open_attempts(conn, run_id, finished_at=finished_at)
    conn.commit()
    logger.log(1, "marked run %s as stopped (%s)", run_id, reason)


def reconcile_stale_runs(
    conn: sqlite3.Connection,
    *,
    project_id: str | None = None,
    dry_run: bool = False,
) -> StaleRunReconciliation:
    """Mark orphaned ``running`` runs stopped when the master PID is gone."""

    query = "SELECT id, pid FROM runs WHERE status = 'running'"
    params: list[str] = []
    if project_id:
        query += " AND project_id = ?"
        params.append(project_id)

    rows = conn.execute(query, params).fetchall()
    stopped: list[str] = []
    for row in rows:
        run_id = str(row["id"])
        pid = row["pid"]
        pid_value = int(pid) if pid is not None else None
        if pid_value is not None and process_exists(pid_value):
            continue
        stopped.append(run_id)
        if dry_run:
            continue
        mark_run_interrupted(conn, run_id)

    return StaleRunReconciliation(stopped_run_ids=tuple(stopped))
