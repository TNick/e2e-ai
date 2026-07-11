"""Persist repair-run metadata on the state database."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime

from ..config import EffectiveConfig


def _now() -> str:
    return datetime.now(tz=UTC).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def create_repair_run(conn: sqlite3.Connection, config: EffectiveConfig) -> str:
    """Insert a top-level repair run."""

    run_id = _new_id("run")
    conn.execute(
        """
        INSERT INTO runs (id, project_id, started_at, status, reason)
        VALUES (?, ?, ?, 'running', NULL)
        """,
        (run_id, config.project_id, _now()),
    )
    conn.commit()
    return run_id


def finish_repair_run(
    conn: sqlite3.Connection,
    run_id: str,
    status: str,
    reason: str | None,
) -> None:
    """Finish a top-level repair run."""

    conn.execute(
        "UPDATE runs SET finished_at = ?, status = ?, reason = ? WHERE id = ?",
        (_now(), status, reason, run_id),
    )
    conn.commit()


def record_repair_plan(
    conn: sqlite3.Connection,
    *,
    test_id: str,
    failure_packet_id: str,
    agent_id: str,
    plan_text: str,
) -> str:
    """Persist a generated plan."""

    plan_id = _new_id("plan")
    conn.execute(
        """
        INSERT INTO repair_plans (
            id, test_id, failure_packet_id, agent_id, plan_text,
            result_json, created_at
        ) VALUES (?, ?, ?, ?, ?, NULL, ?)
        """,
        (plan_id, test_id, failure_packet_id, agent_id, plan_text, _now()),
    )
    conn.commit()
    return plan_id


def set_plan_outcome(conn: sqlite3.Connection, plan_id: str, outcome: str) -> None:
    """Record how a plan turned out."""

    conn.execute(
        "UPDATE repair_plans SET result_json = ? WHERE id = ?",
        (json.dumps({"outcome": outcome}), plan_id),
    )
    conn.commit()


def record_agent_invocation(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    role: str,
    agent_id: str,
    command: list[str],
    status: str,
    exit_code: int | None,
    test_id: str | None = None,
    stdout_path: str | None = None,
    stderr_path: str | None = None,
) -> str:
    """Persist one agent invocation."""

    invocation_id = _new_id("agent")
    conn.execute(
        """
        INSERT INTO agent_invocations (
            id, run_id, test_id, role, agent_id, command_json, status,
            started_at, finished_at, exit_code, stdout_path, stderr_path
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            invocation_id,
            run_id,
            test_id,
            role,
            agent_id,
            json.dumps(command),
            status,
            _now(),
            _now(),
            exit_code,
            stdout_path,
            stderr_path,
        ),
    )
    conn.commit()
    return invocation_id


def has_ever_passed(conn: sqlite3.Connection, test_id: str) -> bool:
    """Return whether this test has passed in any prior attempt."""

    row = conn.execute(
        "SELECT 1 FROM attempts WHERE test_id = ? AND status = 'passed' LIMIT 1",
        (test_id,),
    ).fetchone()
    return row is not None
