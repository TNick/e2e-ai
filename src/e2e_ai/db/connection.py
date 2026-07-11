"""SQLite connection helpers for the e2e-ai state database."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)


def open_database(path: Path) -> sqlite3.Connection:
    """Open the state database with required pragmas."""

    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    logger.log(1, "opened state database at %s", path)
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run database work in a transaction."""

    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        logger.debug("rolled back database transaction", exc_info=True)
        raise
