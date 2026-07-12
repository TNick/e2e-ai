"""Create and run the monitor ASGI app.

FastAPI/uvicorn are the optional ``monitor`` extra. :func:`ensure_monitor_extra`
gives a clear install hint when they are missing; the app is only built when the
extra is present.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .models import MonitorInfo
from .processes import ProcessManager
from .store import MonitorError, MonitorStore

STATIC_DIR = Path(__file__).resolve().parent / "static"

MISSING_EXTRA_MESSAGE = (
    'Install the monitor extra: pip install "e2e-ai[monitor]"'
)


def monitor_extra_available() -> bool:
    """Return whether FastAPI and uvicorn are importable."""

    from importlib.util import find_spec

    return find_spec("fastapi") is not None and find_spec("uvicorn") is not None


def ensure_monitor_extra() -> None:
    """Raise :class:`MonitorError` when the monitor extra is not installed."""

    if not monitor_extra_available():
        raise MonitorError(MISSING_EXTRA_MESSAGE)


def create_app(
    *,
    store: MonitorStore,
    processes: ProcessManager,
    info: MonitorInfo,
    config_full: dict | None = None,
):
    """Build the FastAPI application (API under /api + bundled static UI)."""

    ensure_monitor_extra()
    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles

    from .api import register_routes

    app = FastAPI(title="e2e-ai monitor", docs_url=None, redoc_url=None)
    register_routes(
        app,
        store=store,
        processes=processes,
        info=info,
        config_full=config_full,
    )

    if STATIC_DIR.is_dir() and (STATIC_DIR / "index.html").is_file():
        app.mount(
            "/", StaticFiles(directory=str(STATIC_DIR), html=True), name="ui"
        )
    else:  # pragma: no cover - only when assets were not shipped

        @app.get("/")
        def _no_ui() -> dict[str, str]:
            return {
                "message": (
                    "Monitor API is running; static UI assets are not built."
                ),
                "api": "/api/health",
            }

    return app


def build_monitor(
    *,
    db_path: Path,
    project_root: Path,
    state_dir: Path,
    project_id: str,
    host: str,
    port: int,
    refresh_ms: int,
    python_executable: str | None = None,
    config_full: dict | None = None,
):
    """Assemble store, process manager, info, and the ASGI app."""

    ensure_monitor_extra()
    store = MonitorStore(db_path)
    processes = ProcessManager(
        project_root=project_root,
        state_dir=state_dir,
        python_executable=python_executable or sys.executable,
    )
    info = MonitorInfo(
        project_id=project_id,
        project_root=str(project_root),
        db_path=str(db_path),
        refresh_ms=refresh_ms,
        host=host,
        port=port,
    )
    app = create_app(
        store=store, processes=processes, info=info, config_full=config_full
    )
    return app, store, processes, info


def run_server(
    app, *, host: str, port: int
) -> None:  # pragma: no cover - network
    """Run the app with uvicorn (blocking)."""

    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="warning")
