"""FastAPI routes for the read-only state monitor.

FastAPI is an optional dependency (the ``monitor`` extra); it is imported here,
so importing this module fails cleanly with a helpful message when the extra is
missing. Every database access is read-only; the only side effect is launching
allowlisted e2e-ai commands through :class:`ProcessManager`.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from .commands import (
    CommandValidationError,
    build_argv,
    command_schema,
    get_command,
)
from .models import MonitorInfo
from .processes import ProcessManager
from .store import MonitorError, MonitorStore

try:
    from fastapi import Body, FastAPI, HTTPException, Query, Request
    from fastapi.responses import StreamingResponse
except ModuleNotFoundError as exc:  # pragma: no cover - exercised via server.py
    raise MonitorError(
        'the monitor extra is not installed: pip install "e2e-ai[monitor]"'
    ) from exc


def register_routes(
    app: FastAPI,
    *,
    store: MonitorStore,
    processes: ProcessManager,
    info: MonitorInfo,
    config_full: dict | None = None,
) -> None:
    """Attach all ``/api`` routes to ``app``."""

    def _guard(fn):
        try:
            return fn()
        except MonitorError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        data = store.health()
        data["monitor"] = info.as_dict()
        return data

    @app.get("/api/config")
    def config() -> dict[str, Any]:
        # The full effective config (built-in defaults + user + project),
        # available only when a project config was resolvable.
        return {"available": config_full is not None, "config": config_full}

    @app.get("/api/summary")
    def summary() -> dict[str, Any]:
        return _guard(store.summary)

    @app.get("/api/runs")
    def runs(limit: int = Query(50), offset: int = Query(0)) -> dict[str, Any]:
        return _guard(lambda: store.list_runs(limit=limit, offset=offset))

    @app.get("/api/runs/{run_id}")
    def run(run_id: str) -> dict[str, Any]:
        data = _guard(lambda: store.get_run(run_id))
        if data is None:
            raise HTTPException(status_code=404, detail="run not found")
        return data

    @app.get("/api/tests")
    def tests() -> dict[str, Any]:
        return {
            "items": _guard(
                lambda: store.list_tests(project_id=info.project_id or None)
            )
        }

    @app.get("/api/tests/{test_id}")
    def test(test_id: str) -> dict[str, Any]:
        data = _guard(lambda: store.get_test(test_id))
        if data is None:
            raise HTTPException(status_code=404, detail="test not found")
        return data

    @app.get("/api/attempts/{attempt_id}")
    def attempt(attempt_id: str) -> dict[str, Any]:
        data = _guard(lambda: store.get_attempt(attempt_id))
        if data is None:
            raise HTTPException(status_code=404, detail="attempt not found")
        return data

    @app.get("/api/failures/{packet_id}")
    def failure(packet_id: str) -> dict[str, Any]:
        data = _guard(lambda: store.get_failure(packet_id))
        if data is None:
            raise HTTPException(
                status_code=404, detail="failure packet not found"
            )
        return data

    @app.get("/api/agents")
    def agents(limit: int = Query(100)) -> dict[str, Any]:
        return {"items": _guard(lambda: store.list_agents(limit=limit))}

    @app.get("/api/agents/{invocation_id}")
    def agent(invocation_id: str) -> dict[str, Any]:
        data = _guard(lambda: store.get_agent(invocation_id))
        if data is None:
            raise HTTPException(
                status_code=404,
                detail="agent invocation not found",
            )
        return data

    @app.get("/api/shards")
    def shards() -> dict[str, Any]:
        return {"items": _guard(store.active_shards)}

    # ── commands ─────────────────────────────────────────────────────────────
    @app.get("/api/commands")
    def commands() -> dict[str, Any]:
        return {"items": command_schema()}

    @app.post("/api/commands/{command_id}/runs")
    def start_command(
        command_id: str,
        payload: dict[str, Any] = Body(default_factory=dict),
    ) -> dict[str, Any]:
        try:
            command = get_command(command_id)
            values = dict(payload.get("options", payload) or {})
            values.setdefault("project_root", info.project_root)
            argv = build_argv(
                command_id,
                values,
                python_executable=processes.python_executable,
            )
        except CommandValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            run_id = processes.launch(command, argv)
        except MonitorError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"command_run_id": run_id, "argv": argv}

    @app.get("/api/command-runs")
    def command_runs() -> dict[str, Any]:
        return {"items": processes.list_runs()}

    @app.get("/api/command-runs/{run_id}")
    def command_run(run_id: str) -> dict[str, Any]:
        data = processes.get_run(run_id)
        if data is None:
            raise HTTPException(status_code=404, detail="command run not found")
        return data

    @app.get("/api/command-runs/{run_id}/output")
    def command_run_output(run_id: str) -> dict[str, Any]:
        if processes.get_run(run_id) is None:
            raise HTTPException(status_code=404, detail="command run not found")
        return {
            "command_run_id": run_id,
            "output": processes.read_output(run_id),
        }

    # ── events (SSE) ─────────────────────────────────────────────────────────
    @app.get("/api/events")
    async def events(
        request: Request,
        limit: int | None = Query(None),
    ) -> StreamingResponse:
        interval = max(0.1, info.refresh_ms / 1000)

        async def gen():
            sent = 0
            while True:
                if await request.is_disconnected():
                    break
                try:
                    revision = store.state_revision()
                    active = store.summary().get("active_attempts", 0)
                except MonitorError:
                    revision, active = "", 0
                event = {
                    "type": "state_changed",
                    "revision": f"{revision}|{processes.change_seq}",
                    "active_attempts": active,
                }
                yield f"data: {json.dumps(event)}\n\n"
                sent += 1
                if limit is not None and sent >= limit:
                    break
                await asyncio.sleep(interval)

        return StreamingResponse(gen(), media_type="text/event-stream")
