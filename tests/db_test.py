"""Tests for the e2e-ai state database schema."""

from __future__ import annotations

from pathlib import Path

from e2e_ai.db.connection import open_database, transaction
from e2e_ai.db.migrations import (
    SCHEMA_VERSION,
    apply_schema,
    current_schema_version,
    ensure_database,
)


class TestSchema:
    """Database schema creation."""

    def test_schema_applies_cleanly(self, tmp_path: Path) -> None:
        db_path = tmp_path / "state.sqlite3"
        conn = ensure_database(db_path)
        try:
            assert current_schema_version(conn) == SCHEMA_VERSION
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            assert tables >= {
                "schema_version",
                "projects",
                "tests",
                "runs",
                "attempts",
                "failure_packets",
                "repair_plans",
                "agent_invocations",
            }
            columns = {row[1] for row in conn.execute("PRAGMA table_info(tests)")}
            assert "is_stale" in columns
        finally:
            conn.close()

    def test_foreign_keys_are_enabled(self, tmp_path: Path) -> None:
        conn = open_database(tmp_path / "state.sqlite3")
        try:
            with transaction(conn):
                apply_schema(conn)
            row = conn.execute("PRAGMA foreign_keys").fetchone()
            assert row is not None and int(row[0]) == 1
        finally:
            conn.close()

    def test_ensure_database_is_idempotent(self, tmp_path: Path) -> None:
        db_path = tmp_path / "state.sqlite3"
        first = ensure_database(db_path)
        first.close()
        second = ensure_database(db_path)
        try:
            assert current_schema_version(second) == SCHEMA_VERSION
        finally:
            second.close()

    def test_schema_includes_failover_columns(self, tmp_path: Path) -> None:
        conn = ensure_database(tmp_path / "state.sqlite3")
        try:
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(agent_invocations)")
            }
            assert "provider_order_json" in columns
            assert "exit_class" in columns
            assert "switch_reason" in columns
            assert "failover_retry" in columns
        finally:
            conn.close()
