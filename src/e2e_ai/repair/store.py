"""Persist repair-loop history (runs, attempts, failures, plans)."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from ..models import FailureInfo, TestStatus


def _now() -> str:
    return datetime.now(tz=UTC).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _signature(error_message: str) -> str:
    """Return a stable signature that groups similar failures.

    Volatile bits (numbers, hex ids, absolute paths, timestamps) are stripped so
    the same underlying error collapses to one signature across runs.
    """

    text = (error_message or "").strip().splitlines()
    head = text[0] if text else ""
    normalized = re.sub(r"0x[0-9a-fA-F]+|\d+", "#", head.lower())
    normalized = re.sub(r"[a-z]:[\\/][^\s]+|/[^\s]+", "<path>", normalized)
    digest = hashlib.blake2b(normalized.encode("utf-8"), digest_size=8).hexdigest()
    return digest


@dataclass
class PlanRecord:
    """A previously generated repair plan and how it turned out."""

    id: str
    created_at: str
    agent_id: str
    plan_text: str
    outcome: str


class RepairStore:
    """History side of the state database, sharing the inventory schema."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def _commit(self) -> None:
        self._conn.commit()

    # ── runs ────────────────────────────────────────────────────────────────
    def start_run(self, project_id: str, *, reason: str | None = None) -> str:
        run_id = _new_id("run")
        self._conn.execute(
            """
            INSERT INTO runs (id, project_id, started_at, status, reason)
            VALUES (?, ?, ?, 'running', ?)
            """,
            (run_id, project_id, _now(), reason),
        )
        self._commit()
        return run_id

    def finish_run(
        self, run_id: str, *, status: str, reason: str | None = None
    ) -> None:
        self._conn.execute(
            "UPDATE runs SET finished_at = ?, status = ?, reason = ? WHERE id = ?",
            (_now(), status, reason, run_id),
        )
        self._commit()

    # ── attempts ────────────────────────────────────────────────────────────
    # Attempt rows are created/finished by ``e2e_ai.runner.store``; this store
    # only reads them (below) and owns failures, plans, and agent invocations.
    def has_ever_passed(self, test_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM attempts WHERE test_id = ? AND status = 'passed' LIMIT 1",
            (test_id,),
        ).fetchone()
        return row is not None

    # ── failure packets ─────────────────────────────────────────────────────
    def record_failure(self, attempt_id: str, failure: FailureInfo) -> str:
        packet_id = _new_id("fail")
        payload = json.dumps(failure.__dict__, default=str)
        self._conn.execute(
            """
            INSERT INTO failure_packets (
                id, attempt_id, signature, error_message, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                packet_id,
                attempt_id,
                _signature(failure.error_message),
                failure.error_message[:8000],
                payload,
                _now(),
            ),
        )
        self._commit()
        return packet_id

    def previous_failures(self, test_id: str, *, limit: int = 10) -> list[dict]:
        """Return recorded failures for a test, newest first."""

        rows = self._conn.execute(
            """
            SELECT fp.payload_json, fp.created_at, a.status AS phase
            FROM failure_packets fp
            JOIN attempts a ON a.id = fp.attempt_id
            WHERE a.test_id = ?
            ORDER BY fp.created_at DESC
            LIMIT ?
            """,
            (test_id, limit),
        ).fetchall()
        out: list[dict] = []
        for row in rows:
            try:
                data = json.loads(row["payload_json"])
            except (json.JSONDecodeError, TypeError):
                continue
            data["_phase"] = row["phase"]
            data["_started_at"] = row["created_at"]
            out.append(data)
        return out

    # ── repair plans ────────────────────────────────────────────────────────
    def record_plan(
        self,
        *,
        test_id: str,
        failure_packet_id: str,
        agent_id: str,
        plan_text: str,
    ) -> str:
        plan_id = _new_id("plan")
        self._conn.execute(
            """
            INSERT INTO repair_plans (
                id, test_id, failure_packet_id, agent_id, plan_text,
                result_json, created_at
            ) VALUES (?, ?, ?, ?, ?, NULL, ?)
            """,
            (plan_id, test_id, failure_packet_id, agent_id, plan_text, _now()),
        )
        self._commit()
        return plan_id

    def set_plan_outcome(self, plan_id: str, outcome: str) -> None:
        self._conn.execute(
            "UPDATE repair_plans SET result_json = ? WHERE id = ?",
            (json.dumps({"outcome": outcome}), plan_id),
        )
        self._commit()

    def previous_plans(self, test_id: str, *, limit: int = 10) -> list[PlanRecord]:
        """Return prior plans for a test, oldest first (for prompt context)."""

        rows = self._conn.execute(
            """
            SELECT id, created_at, agent_id, plan_text, result_json
            FROM repair_plans WHERE test_id = ?
            ORDER BY created_at ASC LIMIT ?
            """,
            (test_id, limit),
        ).fetchall()
        records: list[PlanRecord] = []
        for row in rows:
            outcome = "pending"
            if row["result_json"]:
                try:
                    outcome = json.loads(row["result_json"]).get("outcome", "pending")
                except (json.JSONDecodeError, TypeError):
                    pass
            records.append(
                PlanRecord(
                    id=row["id"],
                    created_at=row["created_at"],
                    agent_id=row["agent_id"],
                    plan_text=row["plan_text"],
                    outcome=outcome,
                )
            )
        return records

    # ── agent invocations ───────────────────────────────────────────────────
    def record_agent_invocation(
        self,
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
        started_at: str | None = None,
    ) -> str:
        invocation_id = _new_id("agent")
        self._conn.execute(
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
                started_at or _now(),
                _now(),
                exit_code,
                stdout_path,
                stderr_path,
            ),
        )
        self._commit()
        return invocation_id

    # ── test status ─────────────────────────────────────────────────────────
    def set_test_status(self, test_id: str, status: TestStatus) -> None:
        self._conn.execute(
            "UPDATE tests SET last_status = ? WHERE id = ?",
            (status.value, test_id),
        )
        self._commit()
