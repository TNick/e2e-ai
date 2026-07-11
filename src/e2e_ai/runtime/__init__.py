"""Target support-service lifecycle before Playwright runs."""

from __future__ import annotations

from .base import TargetRuntime
from .docker_compose import (
    DockerComposeRuntime,
    build_runtime_compose_argv,
    create_docker_compose_runtime,
    resolve_runtime_path,
    runtime_cwd,
)
from .health import (
    run_health_checks,
    wait_for_command_health,
    wait_for_http_health,
    wait_for_tcp_health,
)
from .models import RuntimeContext, RuntimeState
from .none import NoTargetRuntime, create_no_target_runtime
from .registry import create_target_runtime
from .session import (
    build_runtime_context,
    managed_target_runtime,
    runtime_env_for_playwright,
)
from .store import runtime_work_dir

__all__ = [
    "DockerComposeRuntime",
    "NoTargetRuntime",
    "RuntimeContext",
    "RuntimeState",
    "TargetRuntime",
    "build_runtime_compose_argv",
    "build_runtime_context",
    "create_docker_compose_runtime",
    "create_no_target_runtime",
    "create_target_runtime",
    "managed_target_runtime",
    "resolve_runtime_path",
    "runtime_cwd",
    "runtime_env_for_playwright",
    "runtime_work_dir",
    "run_health_checks",
    "wait_for_command_health",
    "wait_for_http_health",
    "wait_for_tcp_health",
]
