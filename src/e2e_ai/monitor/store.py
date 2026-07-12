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
EXPECTED_SCHEMA_VERSION = 1

_MISSING_DB_MESSAGE = (
    "No state database found. Run `e2e-ai discover` or `e2e-ai repair` first."
)


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
                f"schema version {version} != expected {EXPECTED_SCHEMA_VERSION}; "
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
        project = self._row("SELECT * FROM projects ORDER BY updated_at DESC LIMIT 1")
        counts = {
            "tests": self._scalar("SELECT COUNT(*) FROM tests") or 0,
            "runnable": self._scalar(
                "SELECT COUNT(*) FROM tests WHERE excluded = 0 AND is_stale = 0"
            )
            or 0,
            "runs": self._scalar("SELECT COUNT(*) FROM runs") or 0,
            "attempts": self._scalar("SELECT COUNT(*) FROM attempts") or 0,
            "failures": self._scalar("SELECT COUNT(*) FROM failure_packets") or 0,
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
        latest_run = self._row("SELECT * FROM runs ORDER BY started_at DESC LIMIT 1")
        active_attempts = (
            self._scalar("SELECT COUNT(*) FROM attempts WHERE finished_at IS NULL") or 0
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
            "(SELECT COUNT(*) FROM attempts a WHERE a.run_id = r.id) AS attempt_count "
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
            "SELECT * FROM agent_invocations WHERE run_id = ? ORDER BY started_at",
            (run_id,),
        )
        for agent in agents:
            agent["command"] = _decode_json(agent.pop("command_json", None))
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
    def list_tests(self, *, include_excluded: bool = True) -> list[dict[str, Any]]:
        where = "" if include_excluded else "WHERE excluded = 0"
        return self._rows(
            f"SELECT * FROM tests {where} ORDER BY spec_file, line, title"
        )

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
        return test

    # ── attempts / failures / agents ────────────────────────────────────────
    def get_attempt(self, attempt_id: str) -> dict[str, Any] | None:
        attempt = self._row(
            "SELECT a.*, t.title, t.spec_file, t.project_name, r.status AS run_status "
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
        packet = self._row("SELECT * FROM failure_packets WHERE id = ?", (packet_id,))
        if packet is None:
            return None
        packet["payload"] = _decode_json(packet.pop("payload_json", None))
        return packet

    def list_agents(self, *, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 1000))
        rows = self._rows(
            "SELECT * FROM agent_invocations ORDER BY started_at DESC LIMIT ?",
            (limit,),
        )
        for row in rows:
            row["command"] = _decode_json(row.pop("command_json", None))
            row.pop("quota_snapshot_json", None)
        return rows

    # ── active shards ───────────────────────────────────────────────────────
    def active_shards(self) -> list[dict[str, Any]]:
        rows = self._rows(
            "SELECT a.*, t.title, t.spec_file, t.project_name, r.status AS run_status "
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
