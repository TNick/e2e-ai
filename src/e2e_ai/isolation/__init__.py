"""Per-test environment isolation backends."""

from __future__ import annotations

from .base import IsolationBackend
from .docker_compose import build_compose_argv, run_compose
from .docker_postgres import (
    DockerPostgresBackend,
    PostgresTemplateConfig,
    build_test_database_name,
    clone_database,
    drop_database,
    ensure_template_database,
    read_postgres_server_version,
    safe_database_name,
    supports_drop_database_force,
)
from .models import EnvironmentLease, IsolationContext
from .none import NoIsolationBackend, create_no_isolation_backend
from .ports import find_free_port_range, port_is_free
from .registry import POSTGRES_BACKENDS, create_isolation_backend

__all__ = [
    "DockerPostgresBackend",
    "EnvironmentLease",
    "IsolationBackend",
    "IsolationContext",
    "NoIsolationBackend",
    "POSTGRES_BACKENDS",
    "PostgresTemplateConfig",
    "build_compose_argv",
    "build_test_database_name",
    "clone_database",
    "create_isolation_backend",
    "create_no_isolation_backend",
    "drop_database",
    "ensure_template_database",
    "find_free_port_range",
    "port_is_free",
    "read_postgres_server_version",
    "run_compose",
    "safe_database_name",
    "supports_drop_database_force",
]
