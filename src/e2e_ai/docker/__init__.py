"""Docker backend helpers for e2e-ai.

The docker backend gives each test a clean PostgreSQL database cloned from a
pristine template, so tests never see each other's writes. See
:mod:`e2e_ai.docker.postgres`. Reusable compose fragments a target project can
include live under ``e2e_ai/docker/assets``.
"""

from __future__ import annotations

from .postgres import PostgresBackend, per_test_db_name

__all__ = ["PostgresBackend", "per_test_db_name"]
