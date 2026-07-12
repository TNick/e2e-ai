"""Lightweight value types for the monitor API.

The store returns plain dictionaries decoded from SQLite, which serialize
directly to JSON. This module holds the small shared shapes that are not just
rows — mainly the server/project info surfaced by ``/api/health``.
"""

from __future__ import annotations

from pathlib import Path

from attrs import asdict, define, field


@define
class MonitorInfo:
    """Static server/project info exposed to the UI."""

    project_id: str = field()
    project_root: str = field()
    db_path: str = field()
    refresh_ms: int = field()
    host: str = field(default="127.0.0.1")
    port: int = field(default=8765)

    def as_dict(self) -> dict:
        return asdict(self)


def to_str_path(value: Path | str) -> str:
    return str(value)
