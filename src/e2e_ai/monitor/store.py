"""Read-only data access for the state monitor.

All SQL lives here. Connections are opened read-only (``mode=ro``) and are
short-lived per query so the monitor never blocks or mutates a running repair
loop.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from ..errors import E2eAiError

# Schema version the monitor understands (see e2e_ai/db/migrations.py).
EXPECTED_SCHEMA_VERSION = 3

_MISSING_DB_MESSAGE = (
    "No state database found. Run `e2e-ai discover` or `e2e-ai repair` first."
)
_MAX_LOG_BYTES = 512_000


def _read_log_file(
    path: str | None, *, max_bytes: int = _MAX_LOG_BYTES
) -> str | None:
    """Return log text from ``path``, or ``None`` when missing/unreadable."""

    if not path:
        return None
    try:
        file_path = Path(path)
        if not file_path.is_file():
            return None
        size = file_path.stat().st_size
        if size <= max_bytes:
            return file_path.read_text(encoding="utf-8", errors="replace")
        with file_path.open("rb") as handle:
            data = handle.read(max_bytes)
        text = data.decode("utf-8", errors="replace")
        omitted = size - max_bytes
        return f"{text}\n… truncated ({omitted} bytes omitted) …"
    except OSError:
        return None


def _decode_plan_outcome(result_json: Any) -> str | None:
    data = _decode_json(result_json)
    if not isinstance(data, dict):
        return None
    outcome = data.get("outcome")
    return str(outcome) if outcome else None


class MonitorError(E2eAiError):
    """Raised when the monitor cannot read the state database."""


def _decode_json(value: Any) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None


class MonitorStore:
    """Read-only accessor over the e2e-ai SQLite state database."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)

    # ── connection ──────────────────────────────────────────────────────────
    def exists(self) -> bool:
        return self.db_path.is_file()

    def _connect(self) -> sqlite3.Connection:
        if not self.exists():
            raise MonitorError(_MISSING_DB_MESSAGE)
        uri = f"file:{self.db_path.as_posix()}?mode=ro"
        try:
            conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        except sqlite3.OperationalError as exc:  # pragma: no cover - defensive
            raise MonitorError(f"could not open state database: {exc}") from exc
        conn.row_factory = sqlite3.Row
        return conn

    def _rows(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()

    def _row(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        rows = self._rows(sql, params)
        return rows[0] if rows else None

    def _scalar(self, sql: str, params: tuple = ()) -> Any:
        row = self._row(sql, params)
        if not row:
            return None
        return next(iter(row.values()))

    # ── health ──────────────────────────────────────────────────────────────
    def schema_version(self) -> int | None:
        try:
            return self._scalar("SELECT MAX(version) AS v FROM schema_version")
        except (MonitorError, sqlite3.OperationalError):
            return None

    def health(self) -> dict[str, Any]:
        if not self.exists():
            return {
                "ok": False,
                "db_path": str(self.db_path),
                "exists": False,
                "schema_version": None,
                "expected_schema_version": EXPECTED_SCHEMA_VERSION,
                "message": _MISSING_DB_MESSAGE,
            }
        version = self.schema_version()
        ok = version == EXPECTED_SCHEMA_VERSION
        message = "ok"
        if version is None:
            message = "state database is missing the schema_version table"
        elif not ok:
            message = (
                f"schema version {version} != expected "
                f"{EXPECTED_SCHEMA_VERSION}; "
                "the monitor may be out of date"
            )
        return {
            "ok": ok,
            "db_path": str(self.db_path),
            "exists": True,
            "schema_version": version,
            "expected_schema_version": EXPECTED_SCHEMA_VERSION,
            "message": message,
        }

    # ── summary ─────────────────────────────────────────────────────────────
    def summary(self) -> dict[str, Any]:
        project = self._row(
            "SELECT * FROM projects ORDER BY updated_at DESC LIMIT 1"
        )
        counts = {
            "tests": self._scalar("SELECT COUNT(*) FROM tests") or 0,
            "runnable": self._scalar(
                "SELECT COUNT(*) FROM tests WHERE excluded = 0 AND is_stale = 0"
            )
            or 0,
            "runs": self._scalar("SELECT COUNT(*) FROM runs") or 0,
            "attempts": self._scalar("SELECT COUNT(*) FROM attempts") or 0,
            "failures": self._scalar("SELECT COUNT(*) FROM failure_packets")
            or 0,
        }
        by_status = {
            row["last_status"] or "unknown": row["n"]
            for row in self._rows(
                "SELECT last_status, COUNT(*) AS n FROM tests "
                "WHERE excluded = 0 GROUP BY last_status"
            )
        }
        active_run = self._row(
            "SELECT * FROM runs WHERE status = 'running' "
            "ORDER BY started_at DESC LIMIT 1"
        )
        latest_run = self._row(
            "SELECT * FROM runs ORDER BY started_at DESC LIMIT 1"
        )
        active_attempts = (
            self._scalar(
                "SELECT COUNT(*) FROM attempts WHERE finished_at IS NULL"
            )
            or 0
        )
        return {
            "project": project,
            "counts": counts,
            "tests_by_status": by_status,
            "active_run": active_run,
            "latest_run": latest_run,
            "active_attempts": active_attempts,
            "revision": self.state_revision(),
        }

    # ── runs ────────────────────────────────────────────────────────────────
    def list_runs(self, *, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        rows = self._rows(
            "SELECT r.*, "
            "(SELECT COUNT(*) FROM attempts a WHERE a.run_id = r.id) "
            "AS attempt_count "
            "FROM runs r ORDER BY r.started_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        total = self._scalar("SELECT COUNT(*) FROM runs") or 0
        return {"items": rows, "total": total, "limit": limit, "offset": offset}

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        run = self._row("SELECT * FROM runs WHERE id = ?", (run_id,))
        if run is None:
            return None
        attempts = self._rows(
            "SELECT a.*, t.title, t.spec_file, t.project_name "
            "FROM attempts a JOIN tests t ON t.id = a.test_id "
            "WHERE a.run_id = ? ORDER BY a.started_at",
            (run_id,),
        )
        agents = self._rows(
            "SELECT * FROM agent_invocations WHERE run_id = ? "
            "ORDER BY started_at",
            (run_id,),
        )
        for agent in agents:
            agent["command"] = _decode_json(agent.pop("command_json", None))
            agent["provider_order"] = _decode_json(
                agent.pop("provider_order_json", None)
            )
        attempt_ids = [a["id"] for a in attempts]
        failures = []
        if attempt_ids:
            placeholders = ",".join("?" for _ in attempt_ids)
            failures = self._rows(
                f"SELECT id, attempt_id, signature, error_message, created_at "
                f"FROM failure_packets WHERE attempt_id IN ({placeholders}) "
                f"ORDER BY created_at",
                tuple(attempt_ids),
            )
        run["attempts"] = attempts
        run["agents"] = agents
        run["failures"] = failures
        return run

    # ── tests ───────────────────────────────────────────────────────────────
    def _normalize_test_counts(self, row: dict[str, Any]) -> dict[str, Any]:
        test = dict(row)
        test["run_count"] = int(test.get("run_count") or 0)
        test["failure_count"] = int(test.get("failure_count") or 0)
        return test

    def list_tests(
        self,
        *,
        include_excluded: bool = True,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if not include_excluded:
            clauses.append("t.excluded = 0")
        if project_id:
            clauses.append("t.project_id = ?")
            params.append(project_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._rows(
            f"""
            SELECT
                t.*,
                COALESCE(h.run_count, 0) AS run_count,
                COALESCE(h.failure_count, 0) AS failure_count
            FROM tests t
            LEFT JOIN (
                SELECT
                    a.test_id,
                    COUNT(a.id) AS run_count,
                    COUNT(fp.id) AS failure_count
                FROM attempts a
                LEFT JOIN failure_packets fp ON fp.attempt_id = a.id
                GROUP BY a.test_id
            ) h ON h.test_id = t.id
            {where}
            ORDER BY t.spec_file, t.line, t.line IS NULL, t.title, t.id
            """,
            tuple(params),
        )
        return [self._normalize_test_counts(row) for row in rows]

    def get_test(self, test_id: str) -> dict[str, Any] | None:
        test = self._row("SELECT * FROM tests WHERE id = ?", (test_id,))
        if test is None:
            return None
        test["attempts"] = self._rows(
            "SELECT * FROM attempts WHERE test_id = ? ORDER BY started_at DESC",
            (test_id,),
        )
        test["plans"] = self._rows(
            "SELECT id, agent_id, created_at, result_json FROM repair_plans "
            "WHERE test_id = ? ORDER BY created_at",
            (test_id,),
        )
        counts = self._row(
            """
            SELECT
                COUNT(a.id) AS run_count,
                COUNT(fp.id) AS failure_count
            FROM attempts a
            LEFT JOIN failure_packets fp ON fp.attempt_id = a.id
            WHERE a.test_id = ?
            """,
            (test_id,),
        )
        test["run_count"] = int((counts or {}).get("run_count") or 0)
        test["failure_count"] = int((counts or {}).get("failure_count") or 0)
        test["agents"] = self._list_agents_for_test(test_id)
        return test

    def _list_agents_for_test(self, test_id: str) -> list[dict[str, Any]]:
        rows = self._rows(
            "SELECT * FROM agent_invocations WHERE test_id = ? "
            "ORDER BY started_at",
            (test_id,),
        )
        return [self._normalize_agent_row(row) for row in rows]

    # ── attempts / failures / agents ────────────────────────────────────────
    def get_attempt(self, attempt_id: str) -> dict[str, Any] | None:
        attempt = self._row(
            "SELECT a.*, t.title, t.spec_file, t.project_name, "
            "r.status AS run_status "
            "FROM attempts a JOIN tests t ON t.id = a.test_id "
            "JOIN runs r ON r.id = a.run_id WHERE a.id = ?",
            (attempt_id,),
        )
        if attempt is None:
            return None
        attempt["failures"] = self._rows(
            "SELECT id, signature, error_message, created_at "
            "FROM failure_packets WHERE attempt_id = ? ORDER BY created_at",
            (attempt_id,),
        )
        return attempt

    def get_failure(self, packet_id: str) -> dict[str, Any] | None:
        packet = self._row(
            "SELECT * FROM failure_packets WHERE id = ?", (packet_id,)
        )
        if packet is None:
            return None
        packet["payload"] = _decode_json(packet.pop("payload_json", None))
        return packet

    def _normalize_agent_row(self, row: dict[str, Any]) -> dict[str, Any]:
        agent = dict(row)
        agent["command"] = _decode_json(agent.pop("command_json", None))
        agent["provider_order"] = _decode_json(
            agent.pop("provider_order_json", None)
        )
        agent.pop("quota_snapshot_json", None)
        return agent

    def _repair_plan_for_invocation(
        self,
        *,
        test_id: str,
        agent_id: str,
        started_at: str,
    ) -> dict[str, Any] | None:
        plan = self._row(
            "SELECT id, plan_text, created_at, result_json FROM repair_plans "
            "WHERE test_id = ? AND agent_id = ? AND created_at >= ? "
            "ORDER BY created_at ASC LIMIT 1",
            (test_id, agent_id, started_at),
        )
        if plan is None:
            plan = self._row(
                "SELECT id, plan_text, created_at, result_json "
                "FROM repair_plans "
                "WHERE test_id = ? AND agent_id = ? "
                "ORDER BY ABS(julianday(created_at) - "
                "julianday(?)) ASC LIMIT 1",
                (test_id, agent_id, started_at),
            )
        if plan is None:
            return None
        plan["outcome"] = _decode_plan_outcome(plan.pop("result_json", None))
        return plan

    def list_agents(self, *, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 1000))
        rows = self._rows(
            "SELECT a.*, t.title AS test_title "
            "FROM agent_invocations a "
            "LEFT JOIN tests t ON t.id = a.test_id "
            "ORDER BY a.started_at DESC LIMIT ?",
            (limit,),
        )
        return [self._normalize_agent_row(row) for row in rows]

    def get_agent(self, invocation_id: str) -> dict[str, Any] | None:
        row = self._row(
            "SELECT a.*, t.title AS test_title, t.spec_file AS test_spec_file "
            "FROM agent_invocations a "
            "LEFT JOIN tests t ON t.id = a.test_id "
            "WHERE a.id = ?",
            (invocation_id,),
        )
        if row is None:
            return None
        agent = self._normalize_agent_row(row)
        stdout_path = agent.get("stdout_path")
        stderr_path = agent.get("stderr_path")
        agent["stdout"] = _read_log_file(stdout_path)
        if stderr_path and stderr_path != stdout_path:
            agent["stderr"] = _read_log_file(stderr_path)
        else:
            agent["stderr"] = None
        test_id = agent.get("test_id")
        if test_id:
            agent["test"] = {
                "id": test_id,
                "title": agent.pop("test_title", None),
                "spec_file": agent.pop("test_spec_file", None),
            }
        else:
            agent.pop("test_title", None)
            agent.pop("test_spec_file", None)
        if agent.get("role") == "planner" and test_id:
            plan = self._repair_plan_for_invocation(
                test_id=test_id,
                agent_id=agent["agent_id"],
                started_at=agent["started_at"],
            )
            if plan is not None:
                agent["repair_plan"] = plan
        return agent

    # ── active shards ───────────────────────────────────────────────────────
    def active_shards(self) -> list[dict[str, Any]]:
        rows = self._rows(
            "SELECT a.*, t.title, t.spec_file, t.project_name, "
            "r.status AS run_status "
            "FROM attempts a JOIN tests t ON t.id = a.test_id "
            "JOIN runs r ON r.id = a.run_id "
            "WHERE a.finished_at IS NULL ORDER BY a.started_at DESC"
        )
        shards: dict[str, dict[str, Any]] = {}
        for row in rows:
            key = row.get("environment_id") or row.get("database_name")
            label = key or "runner-unknown"
            shard = shards.setdefault(
                label,
                {
                    "label": label,
                    "environment_id": row.get("environment_id"),
                    "database_name": row.get("database_name"),
                    "frontend_url": row.get("frontend_url"),
                    "backend_url": row.get("backend_url"),
                    "attempts": [],
                },
            )
            shard["attempts"].append(row)
        return list(shards.values())

    # ── revision (for live refresh) ─────────────────────────────────────────
    def state_revision(self) -> str:
        maxima = (
            self._row(
                "SELECT "
                "(SELECT MAX(started_at) FROM attempts) AS a_start, "
                "(SELECT MAX(finished_at) FROM attempts) AS a_fin, "
                "(SELECT MAX(started_at) FROM agent_invocations) AS ag_start, "
                "(SELECT MAX(finished_at) FROM agent_invocations) AS ag_fin, "
                "(SELECT MAX(started_at) FROM runs) AS r_start"
            )
            or {}
        )
        stamps = [v for v in maxima.values() if v]
        return max(stamps) if stamps else ""
