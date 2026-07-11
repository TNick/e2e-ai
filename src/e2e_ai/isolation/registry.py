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
        raise ConfigError(
            "isolation backend 'fr_two' is not implemented yet; "
            "see research/19. fr-two Integration and Validation.md"
        )
    raise ConfigError(
        f"unknown isolation backend {backend!r}; expected one of: none, "
        "docker_postgres, docker_compose_postgres_template, fr_two"
    )
