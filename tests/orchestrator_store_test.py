"""Tests for orchestrator store agent invocation lifecycle."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from e2e_ai.config import (
    CommandSpec,
    EffectiveConfig,
    IsolationConfig,
    PlaywrightConfig,
    RepairPolicy,
    RolePreferencesConfig,
    RoutingConfig,
)
from e2e_ai.db import ensure_database
from e2e_ai.mcp.models import PlaywrightMcpConfig
from e2e_ai.orchestrator.store import (
    begin_agent_invocation,
    create_repair_run,
    finish_agent_invocation,
    record_agent_invocation,
)


def _config(tmp_path: Path) -> EffectiveConfig:
    return EffectiveConfig(
        project_id="demo",
        project_root=tmp_path,
        state_dir=tmp_path / ".e2e-ai",
        playwright=PlaywrightConfig(
            list_command=CommandSpec(argv=("echo", "list")),
            run_command=CommandSpec(argv=("echo", "run")),
        ),
        agents=(),
        isolation=IsolationConfig(),
        exclude=(),
        repair_policy=RepairPolicy(),
        routing=RoutingConfig(
            role_preferences=RolePreferencesConfig(),
            failover=None,
        ),
        playwright_mcp=PlaywrightMcpConfig(),
    )


def _seed(conn: sqlite3.Connection, tmp_path: Path) -> str:
    conn.execute(
        """
        INSERT INTO projects (
            id, root_path, config_hash, created_at, updated_at
        ) VALUES ('demo', ?, 'hash', 't0', 't0')
        """,
        (str(tmp_path),),
    )
    conn.commit()
    return create_repair_run(conn, _config(tmp_path))


class TestAgentInvocationLifecycle:
    def test_begin_and_finish_leave_distinct_timestamps(self, tmp_path) -> None:
        db = tmp_path / "state.sqlite3"
        conn = ensure_database(db)
        run_id = _seed(conn, tmp_path)
        begin_agent_invocation(
            conn,
            invocation_id="agent_live1",
            run_id=run_id,
            role="implementer",
            agent_id="cursor_auto",
            command=["cursor_auto", "agent_live1"],
            test_id="t1",
            stdout_path=str(tmp_path / "agent_live1.log"),
        )
        row = conn.execute(
            "SELECT status, finished_at FROM agent_invocations WHERE id = ?",
            ("agent_live1",),
        ).fetchone()
        assert row["status"] == "running"
        assert row["finished_at"] is None
        time.sleep(0.01)
        finish_agent_invocation(
            conn,
            "agent_live1",
            status="ok",
            exit_code=0,
        )
        row = conn.execute(
            "SELECT started_at, finished_at, status FROM agent_invocations "
            "WHERE id = ?",
            ("agent_live1",),
        ).fetchone()
        assert row["status"] == "ok"
        assert row["finished_at"] is not None
        assert row["started_at"] <= row["finished_at"]
        conn.close()

    def test_record_helper_still_inserts_completed_row(self, tmp_path) -> None:
        db = tmp_path / "state.sqlite3"
        conn = ensure_database(db)
        run_id = _seed(conn, tmp_path)
        inv_id = record_agent_invocation(
            conn,
            run_id=run_id,
            role="planner",
            agent_id="codex",
            command=["codex", "agent_x"],
            status="ok",
            exit_code=0,
            test_id="t1",
        )
        row = conn.execute(
            "SELECT id, status, finished_at FROM agent_invocations "
            "WHERE id = ?",
            (inv_id,),
        ).fetchone()
        assert row["status"] == "ok"
        assert row["finished_at"] is not None
        conn.close()
