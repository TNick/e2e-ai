"""Local, read-only web monitor for the e2e-ai state database.

`e2e-ai ui` starts a FastAPI server (the optional ``monitor`` extra) that browses
runs/tests/attempts/failures/agents from the SQLite state database and launches
allowlisted e2e-ai commands. Database access is read-only; the UI never mutates
the state database and never runs a shell.
"""

from __future__ import annotations

from .commands import COMMANDS, CommandValidationError, build_argv, command_schema
from .models import MonitorInfo
from .processes import ProcessManager
from .server import (
    MISSING_EXTRA_MESSAGE,
    build_monitor,
    create_app,
    ensure_monitor_extra,
    monitor_extra_available,
    run_server,
)
from .store import MonitorError, MonitorStore

__all__ = [
    "COMMANDS",
    "MISSING_EXTRA_MESSAGE",
    "CommandValidationError",
    "MonitorError",
    "MonitorInfo",
    "MonitorStore",
    "ProcessManager",
    "build_argv",
    "build_monitor",
    "command_schema",
    "create_app",
    "ensure_monitor_extra",
    "monitor_extra_available",
    "run_server",
]
