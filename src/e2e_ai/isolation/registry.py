"""Isolation backend factory."""

from __future__ import annotations

from ..config.models import EffectiveConfig
from ..errors import ConfigError
from .base import IsolationBackend
from .docker_postgres import DockerPostgresBackend
from .none import create_no_isolation_backend

POSTGRES_BACKENDS = frozenset(
    {
        "docker_postgres",
        "docker_compose_postgres_template",
    }
)


def create_isolation_backend(config: EffectiveConfig) -> IsolationBackend:
    """Instantiate the configured isolation backend."""

    backend = config.isolation.backend
    if backend == "none":
        return create_no_isolation_backend(config)
    if backend in POSTGRES_BACKENDS:
        return DockerPostgresBackend()
    if backend == "fr_two":
        # Imported lazily: the fr_two adapter imports this isolation package,
        # so a module-level import here forms an import cycle.
        from ..integrations.fr_two.isolation import (
            create_fr_two_isolation_backend,
        )

        return create_fr_two_isolation_backend(config)
    raise ConfigError(
        f"unknown isolation backend {backend!r}; expected one of: none, "
        "docker_postgres, docker_compose_postgres_template, fr_two"
    )
