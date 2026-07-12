"""Tests for stale run reconciliation."""

from __future__ import annotations

import os
import textwrap
from datetime import UTC, datetime

from click.testing import CliRunner

from e2e_ai.cli import build_cli
from e2e_ai.config import load_effective_config
from e2e_ai.db.migrations import SCHEMA_VERSION, ensure_database
from e2e_ai.orchestrator.store import create_repair_run
from e2e_ai.repair.stale_runs import (
    REASON_PROCESS_INTERRUPTED,
    RUN_STATUS_STOPPED,
    reconcile_stale_runs,
)
from e2e_ai.repair.store import RepairStore


def _now() -> str:
    return datetime.now(tz=UTC).isoformat()


def _seed_project(conn) -> None:
    now = _now()
    conn.execute(
        "INSERT INTO projects (id, root_path, config_hash, created_at, updated_at)"
        " VALUES ('demo', '/r', 'h', ?, ?)",
        (now, now),
    )


def _insert_running_run(
    conn,
    run_id: str,
    *,
    pid: int | None,
) -> None:
    conn.execute(
        """
        INSERT INTO runs (id, project_id, started_at, status, pid)
        VALUES (?, 'demo', ?, 'running', ?)
        """,
        (run_id, _now(), pid),
    )


class TestStaleRuns:
    def test_schema_includes_run_pid(self, tmp_path):
        conn = ensure_database(
            tmp_path / "state.sqlite3",
            reconcile_stale_runs=False,
        )
        try:
            assert SCHEMA_VERSION == 3
            columns = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
            assert "pid" in columns
        finally:
            conn.close()

    def test_process_exists_for_current_pid(self):
        from e2e_ai.repair.stale_runs import process_exists

        assert process_exists(os.getpid()) is True
        assert process_exists(None) is False
        assert process_exists(0) is False
        assert process_exists(99_999_999) is False

    def test_reconcile_stops_runs_without_pid(self, tmp_path):
        db = tmp_path / "state.sqlite3"
        conn = ensure_database(db, reconcile_stale_runs=False)
        _seed_project(conn)
        _insert_running_run(conn, "run_orphan", pid=None)
        conn.commit()

        result = reconcile_stale_runs(conn, project_id="demo")
        row = conn.execute(
            "SELECT status, reason, finished_at FROM runs WHERE id = 'run_orphan'"
        ).fetchone()
        conn.close()

        assert result.stopped_run_ids == ("run_orphan",)
        assert row["status"] == RUN_STATUS_STOPPED
        assert row["reason"] == REASON_PROCESS_INTERRUPTED
        assert row["finished_at"] is not None

    def test_reconcile_stops_runs_with_dead_pid(self, tmp_path):
        db = tmp_path / "state.sqlite3"
        conn = ensure_database(db, reconcile_stale_runs=False)
        _seed_project(conn)
        _insert_running_run(conn, "run_dead", pid=99_999_999)
        conn.commit()

        result = reconcile_stale_runs(conn, project_id="demo")
        row = conn.execute(
            "SELECT status, reason FROM runs WHERE id = 'run_dead'"
        ).fetchone()
        conn.close()

        assert result.stopped_run_ids == ("run_dead",)
        assert row["status"] == RUN_STATUS_STOPPED
        assert row["reason"] == REASON_PROCESS_INTERRUPTED

    def test_reconcile_keeps_live_pid(self, tmp_path):
        db = tmp_path / "state.sqlite3"
        conn = ensure_database(db, reconcile_stale_runs=False)
        _seed_project(conn)
        _insert_running_run(conn, "run_live", pid=os.getpid())
        conn.commit()

        result = reconcile_stale_runs(conn, project_id="demo")
        row = conn.execute(
            "SELECT status, finished_at FROM runs WHERE id = 'run_live'"
        ).fetchone()
        conn.close()

        assert result.stopped_run_ids == ()
        assert row["status"] == "running"
        assert row["finished_at"] is None

    def test_reconcile_finishes_open_attempts(self, tmp_path):
        db = tmp_path / "state.sqlite3"
        conn = ensure_database(db, reconcile_stale_runs=False)
        now = _now()
        _seed_project(conn)
        _insert_running_run(conn, "run_open", pid=None)
        conn.execute(
            "INSERT INTO tests ("
            "id, project_id, title, spec_file, raw_list_line,"
            " excluded, is_stale, first_seen_at, last_seen_at) VALUES"
            " ('t1', 'demo', 't1', 'a.spec.ts', 'line', 0, 0, ?, ?)",
            (now, now),
        )
        conn.execute(
            "INSERT INTO attempts ("
            "id, run_id, test_id, attempt_index, status, work_dir, started_at"
            ") VALUES ('att1', 'run_open', 't1', 0, 'running', 'w', ?)",
            (now,),
        )
        conn.commit()

        reconcile_stale_runs(conn, project_id="demo")
        attempt = conn.execute(
            "SELECT status, finished_at FROM attempts WHERE id = 'att1'"
        ).fetchone()
        conn.close()

        assert attempt["status"] == "interrupted"
        assert attempt["finished_at"] is not None

    def test_reconcile_dry_run_leaves_rows_unchanged(self, tmp_path):
        db = tmp_path / "state.sqlite3"
        conn = ensure_database(db, reconcile_stale_runs=False)
        _seed_project(conn)
        _insert_running_run(conn, "run_dry", pid=None)
        conn.commit()

        result = reconcile_stale_runs(conn, project_id="demo", dry_run=True)
        row = conn.execute(
            "SELECT status, finished_at FROM runs WHERE id = 'run_dry'"
        ).fetchone()
        conn.close()

        assert result.stopped_run_ids == ("run_dry",)
        assert row["status"] == "running"
        assert row["finished_at"] is None

    def test_ensure_database_reconciles_on_open(self, tmp_path):
        db = tmp_path / "state.sqlite3"
        conn = ensure_database(db, reconcile_stale_runs=False)
        _seed_project(conn)
        _insert_running_run(conn, "run_auto", pid=None)
        conn.commit()
        conn.close()

        conn = ensure_database(db, project_id="demo")
        row = conn.execute("SELECT status FROM runs WHERE id = 'run_auto'").fetchone()
        conn.close()

        assert row["status"] == RUN_STATUS_STOPPED

    def test_create_repair_run_records_pid(self, tmp_path):
        from e2e_ai.config.models import (
            AgentConfig,
            CommandSpec,
            EffectiveConfig,
            IsolationConfig,
            PlaywrightConfig,
            RepairPolicy,
            RoutingConfig,
        )
        from e2e_ai.mcp.models import PlaywrightMcpConfig

        db = tmp_path / "state.sqlite3"
        conn = ensure_database(db, reconcile_stale_runs=False)
        now = _now()
        conn.execute(
            "INSERT INTO projects (id, root_path, config_hash, created_at, updated_at)"
            " VALUES ('demo', '/r', 'h', ?, ?)",
            (now, now),
        )
        conn.commit()
        config = EffectiveConfig(
            project_id="demo",
            project_root=tmp_path,
            state_dir=tmp_path / ".e2e-ai",
            playwright=PlaywrightConfig(
                cwd=tmp_path,
                list_command=CommandSpec(argv=("echo", "list")),
                run_command=CommandSpec(argv=("echo", "run")),
            ),
            agents=(AgentConfig(id="planner", plugin="claude"),),
            isolation=IsolationConfig(),
            exclude=(),
            repair_policy=RepairPolicy(),
            routing=RoutingConfig(allow_canary=False),
            playwright_mcp=PlaywrightMcpConfig(),
        )
        run_id = create_repair_run(conn, config)
        row = conn.execute("SELECT pid FROM runs WHERE id = ?", (run_id,)).fetchone()
        conn.close()
        assert int(row["pid"]) == os.getpid()

    def test_repair_store_start_run_records_pid(self, tmp_path):
        db = tmp_path / "state.sqlite3"
        conn = ensure_database(db, reconcile_stale_runs=False)
        now = _now()
        conn.execute(
            "INSERT INTO projects (id, root_path, config_hash, created_at, updated_at)"
            " VALUES ('demo', '/r', 'h', ?, ?)",
            (now, now),
        )
        conn.commit()
        store = RepairStore(conn)
        run_id = store.start_run("demo")
        row = conn.execute("SELECT pid FROM runs WHERE id = ?", (run_id,)).fetchone()
        conn.close()
        assert int(row["pid"]) == os.getpid()


class TestCleanupStaleRunsCli:
    def test_cleanup_stale_runs_reports_stopped(self, tmp_path):
        (tmp_path / "e2e").mkdir()
        (tmp_path / "e2e-ai.yml").write_text(
            textwrap.dedent(
                """
                project: {id: demo}
                state: {dir: .e2e-ai}
                playwright:
                  cwd: e2e
                  list_command: [echo, list]
                  run_command: [echo, run]
                exclude: {tests: []}
                isolation:
                  backend: docker_compose_postgres_template
                  postgres: {db_prefix: demo_}
                agents:
                  planner: {plugin: claude}
                  implementer: {plugin: codex}
                """
            ),
            encoding="utf-8",
        )
        config = load_effective_config(tmp_path)
        db = config.state_dir / "state.sqlite3"
        config.state_dir.mkdir(parents=True)
        conn = ensure_database(db, reconcile_stale_runs=False)
        _seed_project(conn)
        _insert_running_run(conn, "run_cli", pid=None)
        conn.commit()
        conn.close()

        runner = CliRunner()
        result = runner.invoke(
            build_cli(),
            ["cleanup", "--project-root", str(tmp_path), "--stale-runs"],
        )
        assert result.exit_code == 0
        assert "Stopped 1 stale run(s)." in result.output
        assert "run_cli" in result.output

        conn = ensure_database(db, reconcile_stale_runs=False)
        row = conn.execute("SELECT status FROM runs WHERE id = 'run_cli'").fetchone()
        conn.close()
        assert row["status"] == RUN_STATUS_STOPPED
