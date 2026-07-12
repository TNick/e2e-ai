"""Tests for the state monitor: store, commands, launcher, API, and CLI wiring."""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from e2e_ai.db.migrations import ensure_database
from e2e_ai.monitor import build_argv, command_schema
from e2e_ai.monitor.commands import COMMANDS, CommandValidationError, get_command
from e2e_ai.monitor.processes import ProcessManager
from e2e_ai.monitor.server import build_monitor
from e2e_ai.monitor.store import MonitorError, MonitorStore


def _now() -> str:
    return datetime.now(tz=UTC).isoformat()


def _seed(db_path: Path) -> None:
    conn = ensure_database(db_path)
    now = _now()
    conn.execute(
        "INSERT INTO projects (id, root_path, config_hash, created_at, updated_at)"
        " VALUES ('demo','/r','h',?,?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO tests (id, project_id, title, spec_file, raw_list_line,"
        " excluded, is_stale, first_seen_at, last_seen_at, last_status) VALUES"
        " ('t1','demo','logs in','a.spec.ts','a',0,0,?,?,'failing')",
        (now, now),
    )
    conn.execute(
        "INSERT INTO runs (id, project_id, started_at, status) VALUES"
        " ('run1','demo',?, 'running')",
        (now,),
    )
    # One finished attempt, two unfinished on different environments.
    conn.execute(
        "INSERT INTO attempts (id, run_id, test_id, attempt_index, status, work_dir,"
        " environment_id, database_name, started_at, finished_at, exit_code) VALUES"
        " ('a0','run1','t1',0,'failed','w','env-a','db_a',?,?,1)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO attempts (id, run_id, test_id, attempt_index, status, work_dir,"
        " environment_id, database_name, started_at) VALUES"
        " ('a1','run1','t1',1,'running','w','env-a','db_a',?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO attempts (id, run_id, test_id, attempt_index, status, work_dir,"
        " started_at) VALUES ('a2','run1','t1',2,'running','w',?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO failure_packets (id, attempt_id, signature, error_message,"
        " payload_json, created_at) VALUES ('fp1','a0','sig','boom',?,?)",
        (json.dumps({"suspected_family": "assertion"}), now),
    )
    conn.execute(
        "INSERT INTO agent_invocations (id, run_id, test_id, role, agent_id,"
        " command_json, status, started_at) VALUES"
        " ('ai1','run1','t1','planner','codex',?, 'ok', ?)",
        (json.dumps(["codex"]), now),
    )
    conn.commit()
    conn.close()


# ── store ────────────────────────────────────────────────────────────────────
class TestStore:
    def test_summary_from_seeded_db(self, tmp_path):
        db = tmp_path / "state.sqlite3"
        _seed(db)
        store = MonitorStore(db)
        summary = store.summary()
        assert summary["project"]["id"] == "demo"
        assert summary["counts"]["runs"] == 1
        assert summary["counts"]["attempts"] == 3
        assert summary["active_attempts"] == 2

    def test_active_shard_inference_groups_by_environment(self, tmp_path):
        db = tmp_path / "state.sqlite3"
        _seed(db)
        shards = MonitorStore(db).active_shards()
        labels = {s["label"] for s in shards}
        assert "env-a" in labels  # a1 grouped under env-a
        assert "runner-unknown" in labels  # a2 has no env/db
        env_a = next(s for s in shards if s["label"] == "env-a")
        assert len(env_a["attempts"]) == 1

    def test_missing_database_error(self, tmp_path):
        store = MonitorStore(tmp_path / "nope.sqlite3")
        assert store.exists() is False
        with pytest.raises(MonitorError):
            store.summary()

    def test_health_reports_path_and_schema(self, tmp_path):
        db = tmp_path / "state.sqlite3"
        _seed(db)
        health = MonitorStore(db).health()
        assert health["ok"] is True
        assert health["db_path"] == str(db)
        assert health["schema_version"] == health["expected_schema_version"]

    def test_health_missing_db_is_not_ok(self, tmp_path):
        health = MonitorStore(tmp_path / "nope.sqlite3").health()
        assert health["ok"] is False and health["exists"] is False


# ── commands / argv ──────────────────────────────────────────────────────────
class TestCommands:
    def test_registry_exposes_supported_commands(self):
        ids = {c["id"] for c in command_schema()}
        assert ids == {
            "doctor",
            "discover",
            "run",
            "repair",
            "verify",
            "cleanup",
            "agents-list",
            "agents-doctor",
            "db-template",
        }
        run = get_command("run")
        assert {o.name for o in run.options} >= {
            "project_root",
            "test_id",
            "all",
            "fail_fast",
            "limit",
            "rediscover",
            "start_runtime",
            "shard_min_tests",
        }

    def test_build_argv_run(self):
        argv = build_argv(
            "run",
            {
                "all": True,
                "fail_fast": True,
                "limit": 3,
                "rediscover": False,
                "project_root": "/proj",
            },
            python_executable="PY",
        )
        assert argv[:4] == ["PY", "-m", "e2e_ai", "run"]
        assert "--all" in argv and "--fail-fast" in argv
        assert argv[argv.index("--limit") + 1] == "3"
        assert "--no-rediscover" in argv and "--rediscover" not in argv
        assert argv[argv.index("--project-root") + 1] == "/proj"

    def test_build_argv_repeatable_report(self):
        argv = build_argv(
            "verify", {"report": ["r1.json", "r2.json"]}, python_executable="PY"
        )
        assert argv.count("--report") == 2

    def test_rejects_unknown_command(self):
        with pytest.raises(CommandValidationError):
            build_argv("evil", {})

    def test_rejects_unknown_option(self):
        with pytest.raises(CommandValidationError):
            build_argv("run", {"rm_rf": "/"})

    def test_rejects_non_integer(self):
        with pytest.raises(CommandValidationError):
            build_argv("run", {"limit": "lots"})


# ── launcher ─────────────────────────────────────────────────────────────────
class TestLauncher:
    def test_launch_writes_manifest_status_and_output(self, tmp_path):
        pm = ProcessManager(
            project_root=tmp_path,
            state_dir=tmp_path / ".e2e-ai",
            python_executable=sys.executable,
        )
        run = COMMANDS["doctor"]
        argv = [sys.executable, "-c", "print('hello from launcher')"]
        run_id = pm.launch(run, argv)

        # Wait for the watcher thread to record completion.
        for _ in range(50):
            status = pm.get_run(run_id)
            if status and status.get("status") == "exited":
                break
            time.sleep(0.05)

        run_dir = tmp_path / ".e2e-ai" / "monitor" / "commands" / run_id
        assert (run_dir / "command.json").is_file()
        assert (run_dir / "status.json").is_file()
        assert (run_dir / "output.log").is_file()
        command = json.loads((run_dir / "command.json").read_text())
        assert command["started_by"] == "local-ui"
        assert command["argv"] == argv  # argv list, no shell string
        assert status["status"] == "exited" and status["exit_code"] == 0
        assert "hello from launcher" in pm.read_output(run_id)


# ── API ──────────────────────────────────────────────────────────────────────
def _client(tmp_path):
    from fastapi.testclient import TestClient

    db = tmp_path / "state.sqlite3"
    _seed(db)
    app, store, procs, info = build_monitor(
        db_path=db,
        project_root=tmp_path,
        state_dir=tmp_path / ".e2e-ai",
        project_id="demo",
        host="127.0.0.1",
        port=8765,
        refresh_ms=250,
    )
    return TestClient(app), info


class TestApi:
    def test_health(self, tmp_path):
        client, _ = _client(tmp_path)
        data = client.get("/api/health").json()
        assert data["ok"] is True
        assert data["monitor"]["project_id"] == "demo"
        assert data["monitor"]["refresh_ms"] == 250

    def test_run_detail_includes_attempts_and_agents(self, tmp_path):
        client, _ = _client(tmp_path)
        data = client.get("/api/runs/run1").json()
        assert len(data["attempts"]) == 3
        assert data["agents"][0]["agent_id"] == "codex"
        assert data["failures"][0]["signature"] == "sig"

    def test_commands_endpoint(self, tmp_path):
        client, _ = _client(tmp_path)
        ids = {c["id"] for c in client.get("/api/commands").json()["items"]}
        assert "run" in ids and "repair" in ids

    def test_start_command_launches_safe_argv(self, tmp_path, monkeypatch):
        client, _ = _client(tmp_path)
        captured = {}

        def fake_launch(command, argv):
            captured["argv"] = argv
            return "run-xyz"

        # Patch the process manager the app was built with.
        from e2e_ai.monitor import processes as proc_mod

        monkeypatch.setattr(
            proc_mod.ProcessManager, "launch", lambda self, c, a: fake_launch(c, a)
        )
        resp = client.post("/api/commands/discover/runs", json={"options": {}})
        assert resp.status_code == 200
        assert resp.json()["command_run_id"] == "run-xyz"
        assert captured["argv"][1:3] == ["-m", "e2e_ai"]
        assert "discover" in captured["argv"]

    def test_start_command_rejects_unknown(self, tmp_path):
        client, _ = _client(tmp_path)
        assert client.post("/api/commands/evil/runs", json={}).status_code == 400

    def test_events_emits_state_changed(self, tmp_path):
        client, _ = _client(tmp_path)
        with client.stream("GET", "/api/events?limit=1") as r:
            body = "".join(chunk for chunk in r.iter_text())
        assert '"type": "state_changed"' in body

    def test_index_html_served(self, tmp_path):
        client, _ = _client(tmp_path)
        html = client.get("/").text
        assert "e2e-ai monitor" in html


# ── CLI wiring ───────────────────────────────────────────────────────────────
class TestCliWiring:
    def test_build_monitor_passes_settings(self, tmp_path):
        db = tmp_path / "state.sqlite3"
        _seed(db)
        _app, _store, _procs, info = build_monitor(
            db_path=db,
            project_root=tmp_path,
            state_dir=tmp_path / ".e2e-ai",
            project_id="demo",
            host="0.0.0.0",
            port=9000,
            refresh_ms=1500,
        )
        assert info.host == "0.0.0.0"
        assert info.port == 9000
        assert info.refresh_ms == 1500
        assert info.db_path == str(db)


# ── monitor config (host/port from project yml) ──────────────────────────────
class TestMonitorConfig:
    def _project(self, tmp_path, monitor_yaml=""):
        (tmp_path / "e2e").mkdir()
        (tmp_path / "e2e-ai.yml").write_text(
            "project: {id: demo}\n"
            "playwright:\n"
            "  cwd: e2e\n"
            "  list_command: [echo, list]\n"
            "  run_command: [echo, run]\n" + monitor_yaml,
            encoding="utf-8",
        )
        db = tmp_path / ".e2e-ai" / "state.sqlite3"
        _seed(db)
        return tmp_path, db

    def test_defaults_when_absent(self, tmp_path):
        from e2e_ai.config import load_effective_config

        proj, _ = self._project(tmp_path)
        m = load_effective_config(proj).monitor
        assert (m.host, m.port, m.refresh_ms) == ("127.0.0.1", 8765, 1000)

    def test_parsed_from_yaml(self, tmp_path):
        from e2e_ai.config import load_effective_config

        proj, _ = self._project(
            tmp_path,
            "monitor: {host: 0.0.0.0, port: 9100, refresh_ms: 500, open: true}\n",
        )
        m = load_effective_config(proj).monitor
        assert (m.host, m.port, m.refresh_ms, m.open_browser) == (
            "0.0.0.0",
            9100,
            500,
            True,
        )

    def _run_ui(self, tmp_path, args, monkeypatch):
        from click.testing import CliRunner

        from e2e_ai import monitor as mon

        captured = {}

        def fake_build(**kwargs):
            captured.update(kwargs)
            return (object(), None, None, None)

        monkeypatch.setattr(mon, "build_monitor", fake_build)
        monkeypatch.setattr(mon, "run_server", lambda app, *, host, port: None)
        from e2e_ai.cli import build_cli

        res = CliRunner().invoke(build_cli(), ["ui", *args])
        assert res.exit_code == 0, res.output
        return captured

    def test_ui_uses_config_host_port(self, tmp_path, monkeypatch):
        proj, db = self._project(
            tmp_path, "monitor: {host: 127.0.0.1, port: 9222, refresh_ms: 250}\n"
        )
        cap = self._run_ui(tmp_path, ["--project-root", str(proj)], monkeypatch)
        assert cap["port"] == 9222
        assert cap["refresh_ms"] == 250

    def test_ui_flag_overrides_config(self, tmp_path, monkeypatch):
        proj, db = self._project(tmp_path, "monitor: {host: 127.0.0.1, port: 9222}\n")
        cap = self._run_ui(
            tmp_path, ["--project-root", str(proj), "--port", "9333"], monkeypatch
        )
        assert cap["port"] == 9333


# ── /api/config (Settings "everything") ──────────────────────────────────────
class TestConfigEndpoint:
    def test_config_unavailable_without_config(self, tmp_path):
        client, _ = _client(tmp_path)
        data = client.get("/api/config").json()
        assert data["available"] is False
        assert data["config"] is None

    def test_config_available_when_passed(self, tmp_path):
        from fastapi.testclient import TestClient

        db = tmp_path / "state.sqlite3"
        _seed(db)
        app, *_ = build_monitor(
            db_path=db,
            project_root=tmp_path,
            state_dir=tmp_path / ".e2e-ai",
            project_id="demo",
            host="127.0.0.1",
            port=8765,
            refresh_ms=1000,
            config_full={"project_id": "demo", "isolation": {"backend": "none"}},
        )
        data = TestClient(app).get("/api/config").json()
        assert data["available"] is True
        assert data["config"]["isolation"]["backend"] == "none"
