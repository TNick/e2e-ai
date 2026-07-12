"""Typed configuration records for e2e-ai."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from attrs import define, field

if TYPE_CHECKING:
    from ..mcp.models import PlaywrightMcpConfig


def _default_playwright_mcp() -> PlaywrightMcpConfig:
    """Construct the default MCP config lazily.

    Importing ``mcp`` at module top forms a
    ``config -> mcp -> analysis -> inventory -> config`` import cycle, so the
    import is deferred until a default is actually built (by which point
    ``config.models`` is fully initialized).
    """

    from ..mcp.models import PlaywrightMcpConfig

    return PlaywrightMcpConfig()


@define
class CommandSpec:
    """Command argv and environment for an external process.

    Attributes:
        argv: Command arguments without shell interpretation.
        cwd: Optional working directory relative to the project root.
        env: Additional environment variables.
    """

    argv: tuple[str, ...] = field()
    cwd: str | None = field(default=None)
    env: Mapping[str, str] = field(factory=dict)


@define
class PlaywrightReportEnv:
    """Environment variable names for Playwright report artifacts."""

    json: str = field(default="PLAYWRIGHT_JSON_OUTPUT_NAME")
    blob: str = field(default="PLAYWRIGHT_BLOB_OUTPUT_FILE")


@define
class PlaywrightConfig:
    """Playwright project execution settings."""

    cwd: str = field(default="e2e")
    list_command: CommandSpec | None = field(default=None)
    run_command: CommandSpec | None = field(default=None)
    report_env: PlaywrightReportEnv = field(factory=PlaywrightReportEnv)
    base_url_env: str | None = field(default=None)
    api_base_env: str | None = field(default=None)
    lab_flag_env: str | None = field(default=None)


@define
class AgentConfig:
    """One configured agent plugin or role assignment."""

    id: str = field()
    plugin: str | None = field(default=None)
    profile: str | None = field(default=None)
    enabled: bool = field(default=True)
    executable: str | None = field(default=None)


@define
class PostgresIsolationConfig:
    """Settings for PostgreSQL template-clone isolation backends.

    A pristine template database is created once from ``source_db`` and cloned
    into a per-test database for every run. ``env_template`` maps environment
    variable names to value templates (``{database}`` is substituted with the
    per-test clone name) so the project under test connects to its own clone.
    """

    compose_file: str = field(default="docker/compose.yml")
    service: str = field(default="postgres")
    user: str = field(default="postgres")
    template_db: str = field(default="e2e_ai_pristine")
    source_db: str = field(default="app")
    db_prefix: str = field(default="e2e_ai_")
    env_template: Mapping[str, str] = field(factory=dict)
    compose_project_name: str | None = field(default=None)
    env_file: str | None = field(default=None)
    long_lived_services: tuple[str, ...] = field(factory=tuple)
    one_shot_services: tuple[str, ...] = field(factory=tuple)


@define
class IsolationConfig:
    """Isolation backend settings."""

    backend: str = field(default="none")
    adapter: str | None = field(default=None)
    shard_count: int | None = field(default=None)
    keep_on_failure: bool = field(default=False)
    keep_on_success: bool = field(default=True)
    refresh_template: str = field(default="auto")
    postgres: PostgresIsolationConfig = field(factory=PostgresIsolationConfig)


@define
class RepairPolicy:
    """Retry and escalation policy for the repair loop."""

    max_attempts_per_test: int = field(default=3)
    max_same_signature_attempts: int = field(default=2)
    require_external_blocker_for_successful_stop: bool = field(default=True)
    max_run_seconds: int = field(default=14400)
    max_test_seconds: int = field(default=3600)
    max_agent_seconds: int = field(default=1800)
    max_agent_invocations_per_run: int = field(default=50)
    max_agent_invocations_per_test: int = field(default=8)
    stop_on_first_unsolvable: bool = field(default=False)


@define
class FullVerificationConfig:
    """Optional full-suite verification command."""

    command: CommandSpec | None = field(default=None)


@define
class MonitorConfig:
    """Defaults for the ``e2e-ai ui`` local web monitor.

    CLI flags (``--host`` / ``--port`` / ``--refresh-ms`` / ``--open``) override
    these when passed.
    """

    host: str = field(default="127.0.0.1")
    port: int = field(default=8765)
    refresh_ms: int = field(default=1000)
    open_browser: bool = field(default=False)


@define
class TargetSurfaceConfig:
    """One editable or reference surface within the project."""

    path: str = field(default=".")
    editable: bool = field(default=True)
    role: str | None = field(default=None)


@define
class TargetConfig:
    """Declared edit scope for repair agents."""

    scope: str = field(default="frontend_only")
    surfaces: Mapping[str, TargetSurfaceConfig] = field(factory=dict)


def default_target_config() -> TargetConfig:
    """Return the backward-compatible default target configuration."""

    return TargetConfig(
        scope="frontend_only",
        surfaces={
            "frontend": TargetSurfaceConfig(
                path=".",
                editable=True,
                role="source",
            ),
        },
    )


@define
class RoutingConfig:
    """User-level agent routing defaults."""

    allow_canary: bool = field(default=False)
    canary_cache_seconds: int = field(default=60)
    canary_task_class: str = field(default="short")
    planner_requires_schema: bool = field(default=True)
    schema_retry_limit: int = field(default=1)
    long_task_min_remaining_percent: int = field(default=25)


@define
class RuntimeStartConfig:
    """Docker Compose startup options for target runtime."""

    command: str = field(default="up")
    detach: bool = field(default=True)
    build: bool = field(default=False)
    remove_orphans: bool = field(default=True)
    wait: bool = field(default=True)
    timeout_seconds: int = field(default=180)


@define
class RuntimeStopConfig:
    """Target runtime teardown policy."""

    policy: str = field(default="never")
    command: str = field(default="down")
    remove_volumes: bool = field(default=False)


@define
class RuntimeHealthCheckConfig:
    """One readiness probe for the target runtime."""

    name: str = field()
    kind: str = field()
    timeout_seconds: int = field(default=60)
    url: str | None = field(default=None)
    host: str | None = field(default=None)
    port: int | None = field(default=None)
    argv: tuple[str, ...] = field(factory=tuple)


@define
class DockerComposeRuntimeConfig:
    """Docker Compose target runtime settings."""

    cwd: str = field(default=".")
    project_name: str | None = field(default=None)
    compose_files: tuple[str, ...] = field(factory=tuple)
    env_files: tuple[str, ...] = field(factory=tuple)
    profiles: tuple[str, ...] = field(factory=tuple)
    services: tuple[str, ...] = field(factory=tuple)
    env: Mapping[str, str] = field(factory=dict)
    start: RuntimeStartConfig = field(factory=RuntimeStartConfig)
    stop: RuntimeStopConfig = field(factory=RuntimeStopConfig)
    health_checks: tuple[RuntimeHealthCheckConfig, ...] = field(factory=tuple)


@define
class TargetRuntimeConfig:
    """Target support-service lifecycle settings."""

    backend: str = field(default="none")
    docker_compose: DockerComposeRuntimeConfig | None = field(default=None)


@define
class ProjectConfig:
    """Project-level e2e-ai configuration."""

    project_id: str = field(default="")
    state_dir: str = field(default=".e2e-ai")
    playwright: PlaywrightConfig = field(factory=PlaywrightConfig)
    agents: tuple[AgentConfig, ...] = field(factory=tuple)
    isolation: IsolationConfig = field(factory=IsolationConfig)
    exclude: tuple[str, ...] = field(factory=tuple)
    repair_policy: RepairPolicy = field(factory=RepairPolicy)
    full_verification: FullVerificationConfig | None = field(default=None)
    playwright_mcp: PlaywrightMcpConfig = field(factory=_default_playwright_mcp)
    target: TargetConfig = field(factory=default_target_config)
    target_runtime: TargetRuntimeConfig = field(factory=TargetRuntimeConfig)
    monitor: MonitorConfig = field(factory=MonitorConfig)


@define
class UserConfig:
    """User-level e2e-ai defaults."""

    agents: tuple[AgentConfig, ...] = field(factory=tuple)
    routing: RoutingConfig = field(factory=RoutingConfig)


@define
class EffectiveConfig:
    """Merged and validated configuration used by a run."""

    project_id: str = field()
    project_root: Path = field()
    state_dir: Path = field()
    playwright: PlaywrightConfig = field()
    agents: tuple[AgentConfig, ...] = field()
    isolation: IsolationConfig = field()
    exclude: tuple[str, ...] = field()
    repair_policy: RepairPolicy = field()
    routing: RoutingConfig = field()
    full_verification: FullVerificationConfig | None = field(default=None)
    playwright_mcp: PlaywrightMcpConfig = field(factory=_default_playwright_mcp)
    target: TargetConfig = field(factory=default_target_config)
    target_runtime: TargetRuntimeConfig = field(factory=TargetRuntimeConfig)
    monitor: MonitorConfig = field(factory=MonitorConfig)
    project_config_path: Path | None = field(default=None)
    user_config_path: Path | None = field(default=None)
