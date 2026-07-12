"""Safe launcher for monitor-started e2e-ai commands.

Commands are launched from validated argv lists (never a shell). Each launch
gets a directory under ``.e2e-ai/monitor/commands/<id>/`` with ``command.json``,
``status.json``, and a streamed ``output.log``. An in-memory map tracks live
processes; status files persist for recovery after a monitor restart.
"""

from __future__ import annotations

import json
import subprocess
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .commands import CommandDef
from .store import MonitorError

STATUS_RUNNING = "running"
STATUS_EXITED = "exited"
STATUS_FAILED_TO_START = "failed_to_start"
STATUS_TERMINATED = "terminated"


def _now() -> str:
    return datetime.now(tz=UTC).isoformat()


def _new_run_id() -> str:
    stamp = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{uuid.uuid4().hex[:6]}"


class ProcessManager:
    """Launch and track monitor-started command runs."""

    def __init__(
        self,
        *,
        project_root: Path,
        state_dir: Path,
        python_executable: str,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        self.project_root = Path(project_root)
        self.commands_dir = Path(state_dir) / "monitor" / "commands"
        self.python_executable = python_executable
        self._on_change = on_change
        self._lock = threading.Lock()
        self._procs: dict[str, subprocess.Popen] = {}
        self.change_seq = 0
        self.commands_dir.mkdir(parents=True, exist_ok=True)
        self._recover()

    # ── helpers ─────────────────────────────────────────────────────────────
    def _bump(self) -> None:
        self.change_seq += 1
        if self._on_change is not None:
            try:
                self._on_change()
            except Exception:  # pragma: no cover - defensive
                pass

    def _run_dir(self, run_id: str) -> Path:
        return self.commands_dir / run_id

    def _write_status(self, run_id: str, status: dict[str, Any]) -> None:
        path = self._run_dir(run_id) / "status.json"
        path.write_text(json.dumps(status, indent=2), encoding="utf-8")

    def _read_json(self, path: Path) -> dict[str, Any]:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    # ── recovery ────────────────────────────────────────────────────────────
    def _recover(self) -> None:
        for run_dir in self.commands_dir.glob("*/"):
            status_path = run_dir / "status.json"
            if not status_path.is_file():
                continue
            status = self._read_json(status_path)
            if status.get("status") == STATUS_RUNNING:
                # We lost the child across a restart; we cannot re-attach.
                status["status"] = STATUS_TERMINATED
                status["finished_at"] = status.get("finished_at") or _now()
                self._write_status(run_dir.name, status)

    # ── launch ──────────────────────────────────────────────────────────────
    def has_running(self) -> bool:
        with self._lock:
            return any(p.poll() is None for p in self._procs.values())

    def launch(self, command: CommandDef, argv: list[str]) -> str:
        if not command.concurrent and self.has_running():
            raise MonitorError(
                f"command {command.id!r} cannot run while another "
                "command is active"
            )

        run_id = _new_run_id()
        run_dir = self._run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        started = _now()

        (run_dir / "command.json").write_text(
            json.dumps(
                {
                    "command_run_id": run_id,
                    "command_id": command.id,
                    "argv": argv,
                    "project_root": str(self.project_root),
                    "started_at": started,
                    "started_by": "local-ui",
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        log_path = run_dir / "output.log"
        try:
            log_handle = log_path.open("wb")
            proc = subprocess.Popen(
                argv,
                cwd=str(self.project_root),
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
            )
        except OSError as exc:
            self._write_status(
                run_id,
                {
                    "command_run_id": run_id,
                    "pid": None,
                    "status": STATUS_FAILED_TO_START,
                    "exit_code": None,
                    "started_at": started,
                    "finished_at": _now(),
                    "error": str(exc),
                },
            )
            self._bump()
            raise MonitorError(
                f"failed to start {command.id!r}: {exc}"
            ) from exc

        with self._lock:
            self._procs[run_id] = proc
        self._write_status(
            run_id,
            {
                "command_run_id": run_id,
                "pid": proc.pid,
                "status": STATUS_RUNNING,
                "exit_code": None,
                "started_at": started,
                "finished_at": None,
            },
        )
        self._bump()

        watcher = threading.Thread(
            target=self._wait,
            args=(run_id, proc, log_handle, started),
            daemon=True,
        )
        watcher.start()
        return run_id

    def _wait(
        self,
        run_id: str,
        proc: subprocess.Popen,
        log_handle: Any,
        started: str,
    ) -> None:
        code = proc.wait()
        try:
            log_handle.close()
        except OSError:  # pragma: no cover - defensive
            pass
        self._write_status(
            run_id,
            {
                "command_run_id": run_id,
                "pid": proc.pid,
                "status": STATUS_EXITED,
                "exit_code": code,
                "started_at": started,
                "finished_at": _now(),
            },
        )
        with self._lock:
            self._procs.pop(run_id, None)
        self._bump()

    # ── queries ─────────────────────────────────────────────────────────────
    def _merge_run(self, run_id: str) -> dict[str, Any] | None:
        run_dir = self._run_dir(run_id)
        command = self._read_json(run_dir / "command.json")
        status = self._read_json(run_dir / "status.json")
        if not command and not status:
            return None
        return {**command, **status, "command_run_id": run_id}

    def list_runs(self) -> list[dict[str, Any]]:
        runs: list[dict[str, Any]] = []
        for run_dir in self.commands_dir.glob("*/"):
            merged = self._merge_run(run_dir.name)
            if merged is not None:
                runs.append(merged)
        runs.sort(key=lambda r: r.get("started_at") or "", reverse=True)
        return runs

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        return self._merge_run(run_id)

    def read_output(self, run_id: str, *, max_bytes: int = 200_000) -> str:
        log_path = self._run_dir(run_id) / "output.log"
        if not log_path.is_file():
            return ""
        data = log_path.read_bytes()
        if len(data) > max_bytes:
            data = data[-max_bytes:]
        return data.decode("utf-8", errors="replace")
