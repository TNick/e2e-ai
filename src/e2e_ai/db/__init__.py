"""SQLite state database for e2e-ai inventory and repair history."""

from __future__ import annotations

from pathlib import Path

from ..config.models import EffectiveConfig
from .connection import open_database, transaction
from .migrations import ensure_database

__all__ = [
    "database_path",
    "ensure_database",
    "open_database",
    "transaction",
]


def database_path(config: EffectiveConfig) -> Path:
    """Return the project-local state database path."""

    return config.state_dir / "state.sqlite3"
