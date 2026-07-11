"""Target runtime factory."""

from __future__ import annotations

from ..config.models import EffectiveConfig
from ..errors import ConfigError
from .base import TargetRuntime
from .docker_compose import create_docker_compose_runtime
from .none import create_no_target_runtime


def create_target_runtime(config: EffectiveConfig) -> TargetRuntime:
    """Instantiate the configured target runtime backend."""

    backend = config.target_runtime.backend
    if backend == "none":
        return create_no_target_runtime(config)
    if backend == "docker_compose":
        return create_docker_compose_runtime(config)
    raise ConfigError(
        f"unknown target runtime backend {backend!r}; expected one of: none, "
        "docker_compose"
    )
