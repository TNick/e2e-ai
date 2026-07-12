"""Per-test PostgreSQL database cloning via Docker Compose ``postgres``.

Uses the ``postgres`` service in the target compose stack.

This module re-exports the :mod:`e2e_ai.isolation.docker_postgres` helpers for
backward compatibility. New code should use :mod:`e2e_ai.isolation` directly.
"""

from __future__ import annotations

import re

from ..isolation.docker_postgres import (
    DockerPostgresBackend,
    build_test_database_name,
    safe_database_name,
)

_NORMALIZE_RE = re.compile(r"[^a-z0-9_]")


def per_test_db_name(prefix: str, test_id: str) -> str:
    """Return a PostgreSQL-safe database name for a test."""

    safe = _NORMALIZE_RE.sub("_", test_id.lower())
    name = f"{prefix}{safe}"
    return safe_database_name(name[:63])


# Backward-compatible alias used by the CLI and loop.
PostgresBackend = DockerPostgresBackend

__all__ = [
    "DockerPostgresBackend",
    "PostgresBackend",
    "build_test_database_name",
    "per_test_db_name",
    "safe_database_name",
]
