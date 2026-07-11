"""Schema creation and migration for the e2e-ai state database."""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from .connection import open_database, transaction

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
_SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def _utc_now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _schema_sql() -> str:
    return _SCHEMA_PATH.read_text(encoding="utf-8")


def current_schema_version(conn: sqlite3.Connection) -> int:
    """Return the current schema version."""

    try:
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    except sqlite3.OperationalError:
        return 0
    if row is None or row[0] is None:
        return 0
    return int(row[0])


def apply_schema(conn: sqlite3.Connection) -> None:
    """Apply the initial schema."""

    conn.executescript(_schema_sql())
    conn.execute(
        "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
        (SCHEMA_VERSION, _utc_now_iso()),
    )
    logger.log(1, "applied schema version %d", SCHEMA_VERSION)


def ensure_database(path: Path) -> sqlite3.Connection:
    """Create or migrate the state database."""

    conn = open_database(path)
    version = current_schema_version(conn)
    if version < SCHEMA_VERSION:
        with transaction(conn):
            if version == 0:
                apply_schema(conn)
            else:
                raise RuntimeError(
                    f"unsupported schema version {version}; expected {SCHEMA_VERSION}"
                )
    return conn
