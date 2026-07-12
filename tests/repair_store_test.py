"""Tests for repair history persistence helpers."""

from __future__ import annotations

from datetime import UTC, datetime

from e2e_ai.db.migrations import ensure_database
from e2e_ai.repair.store import RepairStore


def _now() -> str:
    return datetime.now(tz=UTC).isoformat()


def _seed_run_with_attempts(
    db_path, *, run_id: str, attempts: list[tuple[str, str]]
):
    conn = ensure_database(db_path)
    now = _now()
    conn.execute(
        "INSERT INTO projects (id, root_path, config_hash, created_at,"
        " updated_at) VALUES ('demo','/r','h',?,?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO runs (id, project_id, started_at, finished_at, status)"
        " VALUES (?, 'demo', ?, ?, 'failed')",
        (run_id, now, now),
    )
    for test_id, _status in attempts:
        conn.execute(
            "INSERT OR IGNORE INTO tests ("
            "id, project_id, title, spec_file, raw_list_line,"
            " excluded, is_stale, first_seen_at, last_seen_at) VALUES"
            " (?, 'demo', ?, 'a.spec.ts', 'line', 0, 0, ?, ?)",
            (test_id, test_id, now, now),
        )
    for index, (test_id, status) in enumerate(attempts):
        conn.execute(
            "INSERT INTO attempts (id, run_id, test_id, attempt_index, status,"
            " work_dir, started_at) VALUES (?, ?, ?, ?, ?, 'w', ?)",
            (f"att_{run_id}_{index}", run_id, test_id, index, status, now),
        )
    conn.commit()
    conn.close()


class TestPreviousRunFailures:
    def test_latest_finished_run_id_ignores_empty_runs(self, tmp_path):
        db = tmp_path / "state.sqlite3"
        _seed_run_with_attempts(
            db,
            run_id="run_old",
            attempts=[("t1", "failed")],
        )
        conn = ensure_database(db)
        later = _now()
        conn.execute(
            "INSERT INTO runs (id, project_id, started_at, finished_at, status)"
            " VALUES ('run_new', 'demo', ?, ?, 'passed')",
            (later, later),
        )
        conn.commit()
        store = RepairStore(conn)
        assert store.latest_finished_run_id("demo") == "run_old"

    def test_test_ids_not_passed_in_run(self, tmp_path):
        db = tmp_path / "state.sqlite3"
        _seed_run_with_attempts(
            db,
            run_id="run1",
            attempts=[
                ("t_pass", "passed"),
                ("t_fail", "failed"),
                ("t_retry", "failed"),
                ("t_retry", "passed"),
            ],
        )
        conn = ensure_database(db)
        store = RepairStore(conn)
        assert store.test_ids_not_passed_in_run("run1") == {"t_fail"}
