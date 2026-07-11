"""Load, merge, and validate e2e-ai configuration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

import platformdirs
import yaml

from ..errors import ConfigError

if TYPE_CHECKING:
    from ..mcp.models import PlaywrightMcpConfig
from .defaults import (
    DEFAULT_PROJECT_CONFIG,
    DEFAULT_USER_CONFIG,
    DEFAULT_USER_CONFIG_YAML,
)
from .models import (
    AgentConfig,
    CommandSpec,
    DockerComposeRuntimeConfig,
    EffectiveConfig,
    FullVerificationConfig,
    IsolationConfig,
    PlaywrightConfig,
    PlaywrightReportEnv,
    PostgresIsolationConfig,
    ProjectConfig,
    RepairPolicy,
    RoutingConfig,
    RuntimeHealthCheckConfig,
    RuntimeStartConfig,
    RuntimeStopConfig,
    TargetConfig,
    TargetRuntimeConfig,
    TargetSurfaceConfig,
    UserConfig,
    default_target_config,
)
from .schema import PROJECT_CONFIG_NAMES, USER_CONFIG_RELATIVE
from .validation import validate_effective_config

logger = logging.getLogger(__name__)


def default_user_config_path() -> Path:
    """Return the platform user config file path."""

    base = platformdirs.user_config_path("e2e-ai", appauthor=False)
    return Path(base) / USER_CONFIG_RELATIVE[1]


def find_project_config(project_root: Path) -> Path | None:
    """Return the project config file if one exists."""

    start = project_root.resolve()
    for directory in (start, *start.parents):
        for name in PROJECT_CONFIG_NAMES:
            candidate = directory / name
            if candidate.is_file():
                return candidate
    return None


def load_yaml_file(path: Path) -> dict[str, object]:
    """Read a YAML file into a mapping."""

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(f"config file {path} must contain a mapping at the top level")
    return raw


def _require_mapping(
    data: object,
    label: str,
) -> dict[str, object]:
    if not isinstance(data, dict):
        raise ConfigError(f"{label} must be a mapping")
    return data


def _parse_command_spec(
    data: object,
    label: str,
) -> CommandSpec | None:
    if data is None:
        return None
    if isinstance(data, str):
        raise ConfigError(
            f"{label} must be a YAML list of argv tokens, not a shell string"
        )
    if not isinstance(data, list):
        raise ConfigError(f"{label} must be a YAML list of argv tokens")
    argv = tuple(str(part) for part in data)
    return CommandSpec(argv=argv)


def _parse_command_mapping(
    data: object,
    label: str,
) -> CommandSpec | None:
    if data is None:
        return None
    mapping = _require_mapping(data, label)
    argv_raw = mapping.get("argv") or mapping.get("command")
    if argv_raw is None:
        raise ConfigError(f"{label} must include argv or command")
    cwd_raw = mapping.get("cwd")
    env_raw = mapping.get("env")
    cwd = str(cwd_raw) if cwd_raw is not None else None
    env: dict[str, str] = {}
    if env_raw is not None:
        env_mapping = _require_mapping(env_raw, f"{label}.env")
        env = {str(key): str(value) for key, value in env_mapping.items()}
    spec = _parse_command_spec(argv_raw, f"{label}.argv")
    if spec is None:
        raise ConfigError(f"{label} must include argv or command")
    return CommandSpec(argv=spec.argv, cwd=cwd, env=env)


def _parse_playwright_config(data: object) -> PlaywrightConfig:
    if data is None:
        return PlaywrightConfig()
    mapping = _require_mapping(data, "playwright")
    list_command = _parse_command_spec(
        mapping.get("list_command"),
        "playwright.list_command",
    )
    run_command = _parse_command_spec(
        mapping.get("run_command"),
        "playwright.run_command",
    )
    report_env = PlaywrightReportEnv()
    report_raw = mapping.get("report_env")
    if report_raw is not None:
        report_mapping = _require_mapping(report_raw, "playwright.report_env")
        json_name = report_mapping.get("json")
        blob_name = report_mapping.get("blob")
        if json_name is not None:
            report_env = PlaywrightReportEnv(
                json=str(json_name),
                blob=str(report_mapping.get("blob", report_env.blob)),
            )
        elif blob_name is not None:
            report_env = PlaywrightReportEnv(blob=str(blob_name))
    base_url_env = mapping.get("base_url_env")
    api_base_env = mapping.get("api_base_env")
    lab_flag_env = mapping.get("lab_flag_env")
    return PlaywrightConfig(
        cwd=str(mapping.get("cwd", "e2e")),
        list_command=list_command,
        run_command=run_command,
        report_env=report_env,
        base_url_env=str(base_url_env) if base_url_env is not None else None,
        api_base_env=str(api_base_env) if api_base_env is not None else None,
        lab_flag_env=str(lab_flag_env) if lab_flag_env is not None else None,
    )


def _parse_agent_entry(agent_id: str, data: object) -> AgentConfig:
    if isinstance(data, str):
        return AgentConfig(id=agent_id, plugin=data)
    mapping = _require_mapping(data, f"agents.{agent_id}")
    plugin = mapping.get("plugin")
    profile = mapping.get("profile")
    enabled = mapping.get("enabled", True)
    executable = mapping.get("executable")
    return AgentConfig(
        id=agent_id,
        plugin=str(plugin) if plugin is not None else None,
        profile=str(profile) if profile is not None else None,
        enabled=bool(enabled),
        executable=str(executable) if executable is not None else None,
    )


def _parse_agents(data: object) -> tuple[AgentConfig, ...]:
    if data is None:
        return ()
    mapping = _require_mapping(data, "agents")
    return tuple(_parse_agent_entry(agent_id, raw) for agent_id, raw in mapping.items())


def _parse_isolation_config(data: object) -> IsolationConfig:
    if data is None:
        return IsolationConfig()
    mapping = _require_mapping(data, "isolation")
    shard_count = mapping.get("shard_count")
    refresh_template = str(mapping.get("refresh_template", "auto"))
    return IsolationConfig(
        backend=str(mapping.get("backend", "none")),
        adapter=str(mapping["adapter"]) if mapping.get("adapter") else None,
        shard_count=int(shard_count) if shard_count is not None else None,
        keep_on_failure=bool(mapping.get("keep_on_failure", False)),
        keep_on_success=bool(mapping.get("keep_on_success", True)),
        refresh_template=refresh_template,
        postgres=_parse_postgres_isolation(mapping.get("postgres")),
    )


def _parse_postgres_isolation(data: object) -> PostgresIsolationConfig:
    if data is None:
        return PostgresIsolationConfig()
    mapping = _require_mapping(data, "isolation.postgres")
    defaults = PostgresIsolationConfig()
    env_raw = mapping.get("env_template")
    env: dict[str, str] = {}
    if env_raw is not None:
        env_mapping = _require_mapping(env_raw, "isolation.postgres.env_template")
        env = {str(key): str(value) for key, value in env_mapping.items()}
    long_lived = mapping.get("long_lived_services")
    one_shot = mapping.get("one_shot_services")
    return PostgresIsolationConfig(
        compose_file=str(mapping.get("compose_file", defaults.compose_file)),
        service=str(mapping.get("service", defaults.service)),
        user=str(mapping.get("user", defaults.user)),
        template_db=str(mapping.get("template_db", defaults.template_db)),
        source_db=str(mapping.get("source_db", defaults.source_db)),
        db_prefix=str(mapping.get("db_prefix", defaults.db_prefix)),
        env_template=env,
        compose_project_name=(
            str(mapping["compose_project_name"])
            if mapping.get("compose_project_name")
            else None
        ),
        env_file=str(mapping["env_file"]) if mapping.get("env_file") else None,
        long_lived_services=(
            tuple(str(item) for item in long_lived) if long_lived else ()
        ),
        one_shot_services=(tuple(str(item) for item in one_shot) if one_shot else ()),
    )


def _parse_repair_policy(data: object) -> RepairPolicy:
    if data is None:
        return RepairPolicy()
    mapping = _require_mapping(data, "repair_policy")
    defaults = DEFAULT_PROJECT_CONFIG.repair_policy
    max_attempts = mapping.get("max_attempts_per_test", defaults.max_attempts_per_test)
    return RepairPolicy(
        max_attempts_per_test=int(max_attempts),
        max_same_signature_attempts=int(
            mapping.get(
                "max_same_signature_attempts",
                defaults.max_same_signature_attempts,
            )
        ),
        require_external_blocker_for_successful_stop=bool(
            mapping.get(
                "require_external_blocker_for_successful_stop",
                defaults.require_external_blocker_for_successful_stop,
            )
        ),
        max_run_seconds=int(mapping.get("max_run_seconds", defaults.max_run_seconds)),
        max_test_seconds=int(
            mapping.get("max_test_seconds", defaults.max_test_seconds)
        ),
        max_agent_seconds=int(
            mapping.get("max_agent_seconds", defaults.max_agent_seconds)
        ),
        max_agent_invocations_per_run=int(
            mapping.get(
                "max_agent_invocations_per_run",
                defaults.max_agent_invocations_per_run,
            )
        ),
        max_agent_invocations_per_test=int(
            mapping.get(
                "max_agent_invocations_per_test",
                defaults.max_agent_invocations_per_test,
            )
        ),
        stop_on_first_unsolvable=bool(
            mapping.get(
                "stop_on_first_unsolvable",
                defaults.stop_on_first_unsolvable,
            )
        ),
    )


def _parse_playwright_mcp(data: object) -> PlaywrightMcpConfig:
    # Imported lazily: importing ``mcp`` at module top forms a
    # config -> mcp -> analysis -> runner -> config import cycle.
    from ..mcp.models import (
        McpOriginsConfig,
        McpStorageStateConfig,
        PlaywrightMcpConfig,
    )

    if data is None:
        return PlaywrightMcpConfig()
    mapping = _require_mapping(data, "playwright_mcp")
    defaults = PlaywrightMcpConfig()
    roles_raw = mapping.get("roles")
    role_enabled = dict(defaults.role_enabled)
    if roles_raw is not None:
        roles_map = _require_mapping(roles_raw, "playwright_mcp.roles")
        for key, value in roles_map.items():
            role_enabled[str(key)] = bool(value)
    tools_raw = mapping.get("tools")
    tools_allow = defaults.tools_allow
    tools_deny = defaults.tools_deny
    if tools_raw is not None:
        tools_map = _require_mapping(tools_raw, "playwright_mcp.tools")
        allow = tools_map.get("allow")
        deny = tools_map.get("deny")
        if allow is not None:
            tools_allow = tuple(str(item) for item in allow)
        if deny is not None:
            tools_deny = tuple(str(item) for item in deny)
    origins_raw = mapping.get("origins")
    origins = McpOriginsConfig()
    if origins_raw is not None:
        origins_map = _require_mapping(origins_raw, "playwright_mcp.origins")
        extra = origins_map.get("extra_allow")
        origins = McpOriginsConfig(
            from_environment_lease=bool(
                origins_map.get(
                    "from_environment_lease",
                    origins.from_environment_lease,
                )
            ),
            extra_allow=(
                tuple(str(item) for item in extra) if extra is not None else ()
            ),
        )
    storage_raw = mapping.get("storage_state")
    storage_state = McpStorageStateConfig()
    if storage_raw is not None:
        storage_map = _require_mapping(storage_raw, "playwright_mcp.storage_state")
        path = storage_map.get("path")
        storage_state = McpStorageStateConfig(
            mode=str(storage_map.get("mode", storage_state.mode)),
            path=str(path) if path is not None else None,
        )
    caps = mapping.get("capabilities")
    return PlaywrightMcpConfig(
        enabled=bool(mapping.get("enabled", defaults.enabled)),
        version=str(mapping.get("version", defaults.version)),
        package=str(mapping.get("package", defaults.package)),
        transport=str(mapping.get("transport", defaults.transport)),
        browser=str(mapping.get("browser", defaults.browser)),
        headless=bool(mapping.get("headless", defaults.headless)),
        isolated=bool(mapping.get("isolated", defaults.isolated)),
        output_mode=str(mapping.get("output_mode", defaults.output_mode)),
        output_max_mb=int(mapping.get("output_max_mb", defaults.output_max_mb)),
        console_level=str(mapping.get("console_level", defaults.console_level)),
        snapshot_mode=str(mapping.get("snapshot_mode", defaults.snapshot_mode)),
        image_responses=str(mapping.get("image_responses", defaults.image_responses)),
        unrestricted_file_access=bool(
            mapping.get(
                "unrestricted_file_access",
                defaults.unrestricted_file_access,
            )
        ),
        test_id_attribute=str(
            mapping.get("test_id_attribute", defaults.test_id_attribute)
        ),
        capabilities=(
            tuple(str(item) for item in caps)
            if caps is not None
            else defaults.capabilities
        ),
        tools_allow=tools_allow,
        tools_deny=tools_deny,
        role_enabled=role_enabled,
        origins=origins,
        storage_state=storage_state,
        keep_artifacts_on_failure=bool(
            mapping.get(
                "keep_artifacts_on_failure",
                defaults.keep_artifacts_on_failure,
            )
        ),
    )


def _parse_full_verification(data: object) -> FullVerificationConfig | None:
    if data is None:
        return None
    mapping = _require_mapping(data, "full_verification")
    command = _parse_command_spec(
        mapping.get("command"),
        "full_verification.command",
    )
    return FullVerificationConfig(command=command)


def _parse_exclude_patterns(data: object) -> tuple[str, ...]:
    if data is None:
        return ()
    if isinstance(data, list):
        return tuple(str(item) for item in data)
    mapping = _require_mapping(data, "exclude")
    tests = mapping.get("tests", [])
    if not isinstance(tests, list):
        raise ConfigError("exclude.tests must be a list")
    return tuple(str(item) for item in tests)


def _parse_routing_config(data: object) -> RoutingConfig:
    if data is None:
        return RoutingConfig()
    mapping = _require_mapping(data, "routing")
    return RoutingConfig(
        allow_canary=bool(mapping.get("allow_canary", False)),
        canary_cache_seconds=int(mapping.get("canary_cache_seconds", 60)),
        canary_task_class=str(mapping.get("canary_task_class", "short")),
        planner_requires_schema=bool(mapping.get("planner_requires_schema", True)),
        schema_retry_limit=int(mapping.get("schema_retry_limit", 1)),
        long_task_min_remaining_percent=int(
            mapping.get("long_task_min_remaining_percent", 25)
        ),
    )


def _parse_target_config(data: object) -> TargetConfig:
    if data is None:
        return default_target_config()
    mapping = _require_mapping(data, "target")
    scope = str(mapping.get("scope", "frontend_only"))
    surfaces_raw = mapping.get("surfaces")
    if surfaces_raw is None:
        return TargetConfig(
            scope=scope,
            surfaces=dict(default_target_config().surfaces),
        )
    surfaces_map = _require_mapping(surfaces_raw, "target.surfaces")
    surfaces: dict[str, TargetSurfaceConfig] = {}
    for name, raw_surface in surfaces_map.items():
        surface_map = _require_mapping(
            raw_surface,
            f"target.surfaces.{name}",
        )
        role = surface_map.get("role")
        surfaces[str(name)] = TargetSurfaceConfig(
            path=str(surface_map.get("path", ".")),
            editable=bool(surface_map.get("editable", True)),
            role=str(role) if role is not None else None,
        )
    return TargetConfig(scope=scope, surfaces=surfaces)


def _parse_runtime_health_checks(data: object) -> tuple[RuntimeHealthCheckConfig, ...]:
    if data is None:
        return ()
    if not isinstance(data, list):
        raise ConfigError("target_runtime.health_checks must be a list")
    checks: list[RuntimeHealthCheckConfig] = []
    for index, raw in enumerate(data):
        mapping = _require_mapping(raw, f"target_runtime.health_checks[{index}]")
        argv_raw = mapping.get("argv") or mapping.get("command")
        argv: tuple[str, ...] = ()
        if argv_raw is not None:
            spec = _parse_command_spec(
                argv_raw,
                f"target_runtime.health_checks[{index}].argv",
            )
            if spec is not None:
                argv = spec.argv
        port = mapping.get("port")
        checks.append(
            RuntimeHealthCheckConfig(
                name=str(mapping["name"]),
                kind=str(mapping["kind"]),
                timeout_seconds=int(mapping.get("timeout_seconds", 60)),
                url=str(mapping["url"]) if mapping.get("url") is not None else None,
                host=str(mapping["host"]) if mapping.get("host") is not None else None,
                port=int(port) if port is not None else None,
                argv=argv,
            )
        )
    return tuple(checks)


def _parse_docker_compose_runtime(data: object) -> DockerComposeRuntimeConfig:
    if data is None:
        return DockerComposeRuntimeConfig()
    mapping = _require_mapping(data, "target_runtime.docker_compose")
    start_raw = mapping.get("start")
    stop_raw = mapping.get("stop")
    start = RuntimeStartConfig()
    stop = RuntimeStopConfig()
    if start_raw is not None:
        start_map = _require_mapping(start_raw, "target_runtime.start")
        start = RuntimeStartConfig(
            command=str(start_map.get("command", start.command)),
            detach=bool(start_map.get("detach", start.detach)),
            build=bool(start_map.get("build", start.build)),
            remove_orphans=bool(start_map.get("remove_orphans", start.remove_orphans)),
            wait=bool(start_map.get("wait", start.wait)),
            timeout_seconds=int(
                start_map.get("timeout_seconds", start.timeout_seconds)
            ),
        )
    if stop_raw is not None:
        stop_map = _require_mapping(stop_raw, "target_runtime.stop")
        stop = RuntimeStopConfig(
            policy=str(stop_map.get("policy", stop.policy)),
            command=str(stop_map.get("command", stop.command)),
            remove_volumes=bool(stop_map.get("remove_volumes", stop.remove_volumes)),
        )
    env_raw = mapping.get("env")
    env: dict[str, str] = {}
    if env_raw is not None:
        env_mapping = _require_mapping(env_raw, "target_runtime.env")
        env = {str(key): str(value) for key, value in env_mapping.items()}
    compose_files = mapping.get("compose_files", ())
    env_files = mapping.get("env_files", ())
    profiles = mapping.get("profiles", ())
    services = mapping.get("services", ())
    return DockerComposeRuntimeConfig(
        cwd=str(mapping.get("cwd", ".")),
        project_name=(
            str(mapping["project_name"]) if mapping.get("project_name") else None
        ),
        compose_files=tuple(str(item) for item in compose_files),
        env_files=tuple(str(item) for item in env_files),
        profiles=tuple(str(item) for item in profiles),
        services=tuple(str(item) for item in services),
        env=env,
        start=start,
        stop=stop,
        health_checks=_parse_runtime_health_checks(mapping.get("health_checks")),
    )


def _parse_target_runtime(data: object) -> TargetRuntimeConfig:
    if data is None:
        return TargetRuntimeConfig()
    mapping = _require_mapping(data, "target_runtime")
    backend = str(mapping.get("backend", "none"))
    docker_raw = mapping
    if mapping.get("compose_files") is None and mapping.get("docker_compose"):
        docker_raw = mapping.get("docker_compose")
    elif backend == "docker_compose" and mapping.get("compose_files") is not None:
        docker_raw = mapping
    elif backend == "docker_compose":
        docker_raw = mapping
    else:
        docker_raw = mapping.get("docker_compose")
    docker_compose = None
    if backend == "docker_compose":
        docker_compose = _parse_docker_compose_runtime(docker_raw)
    return TargetRuntimeConfig(backend=backend, docker_compose=docker_compose)


def _parse_project_config(data: Mapping[str, object]) -> ProjectConfig:
    project_raw = data.get("project")
    project_id = ""
    if project_raw is not None:
        project_mapping = _require_mapping(project_raw, "project")
        project_id = str(project_mapping.get("id", ""))

    state_raw = data.get("state")
    state_dir = DEFAULT_PROJECT_CONFIG.state_dir
    if state_raw is not None:
        state_mapping = _require_mapping(state_raw, "state")
        state_dir = str(state_mapping.get("dir", state_dir))

    return ProjectConfig(
        project_id=project_id,
        state_dir=state_dir,
        playwright=_parse_playwright_config(data.get("playwright")),
        agents=_parse_agents(data.get("agents")),
        isolation=_parse_isolation_config(data.get("isolation")),
        exclude=_parse_exclude_patterns(data.get("exclude")),
        repair_policy=_parse_repair_policy(
            data.get("repair_policy") or data.get("repair")
        ),
        full_verification=_parse_full_verification(data.get("full_verification")),
        playwright_mcp=_parse_playwright_mcp(data.get("playwright_mcp")),
        target=_parse_target_config(data.get("target")),
        target_runtime=_parse_target_runtime(data.get("target_runtime")),
    )


def _parse_user_config(data: Mapping[str, object]) -> UserConfig:
    return UserConfig(
        agents=_parse_agents(data.get("agents")),
        routing=_parse_routing_config(data.get("routing")),
    )


def _merge_agent_configs(
    user_agents: tuple[AgentConfig, ...],
    project_agents: tuple[AgentConfig, ...],
) -> tuple[AgentConfig, ...]:
    merged: dict[str, AgentConfig] = {agent.id: agent for agent in user_agents}
    for agent in project_agents:
        existing = merged.get(agent.id)
        if existing is None:
            merged[agent.id] = agent
            continue
        merged[agent.id] = AgentConfig(
            id=agent.id,
            plugin=agent.plugin if agent.plugin is not None else existing.plugin,
            profile=agent.profile if agent.profile is not None else existing.profile,
            enabled=agent.enabled,
            executable=(
                agent.executable
                if agent.executable is not None
                else existing.executable
            ),
        )
    return tuple(merged[agent_id] for agent_id in sorted(merged))


def ensure_user_config(path: Path | None = None) -> Path:
    """Create an empty user config file when one does not exist."""

    config_path = path or default_user_config_path()
    if not config_path.is_file():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(DEFAULT_USER_CONFIG_YAML, encoding="utf-8")
        logger.log(1, "created default user config at %s", config_path)
    return config_path


def load_user_config(path: Path | None = None) -> UserConfig:
    """Load user-level config or return defaults."""

    config_path = ensure_user_config(path)
    if not config_path.is_file():
        return DEFAULT_USER_CONFIG
    data = load_yaml_file(config_path)
    parsed = _parse_user_config(data)
    if not parsed.agents:
        return UserConfig(
            agents=DEFAULT_USER_CONFIG.agents,
            routing=parsed.routing,
        )
    return parsed


def load_project_config(project_root: Path) -> ProjectConfig:
    """Load project-level config from the target repository."""

    config_path = find_project_config(project_root)
    if config_path is None:
        return DEFAULT_PROJECT_CONFIG
    data = load_yaml_file(config_path)
    return _parse_project_config(data)


def merge_config(
    user_config: UserConfig,
    project_config: ProjectConfig,
    *,
    project_root: Path,
    project_config_path: Path | None = None,
    user_config_path: Path | None = None,
) -> EffectiveConfig:
    """Merge user and project configuration."""

    merged_agents = _merge_agent_configs(user_config.agents, project_config.agents)
    state_dir = (project_root / project_config.state_dir).resolve()
    full_verification = project_config.full_verification
    return EffectiveConfig(
        project_id=project_config.project_id,
        project_root=project_root.resolve(),
        state_dir=state_dir,
        playwright=project_config.playwright,
        agents=merged_agents,
        isolation=project_config.isolation,
        exclude=project_config.exclude,
        repair_policy=project_config.repair_policy,
        routing=user_config.routing,
        full_verification=full_verification,
        playwright_mcp=project_config.playwright_mcp,
        target=project_config.target,
        target_runtime=project_config.target_runtime,
        project_config_path=project_config_path,
        user_config_path=user_config_path,
    )


def load_effective_config(
    project_root: Path,
    user_config_path: Path | None = None,
) -> EffectiveConfig:
    """Load, merge, and validate all config."""

    resolved_user_path = ensure_user_config(user_config_path)
    user_config = load_user_config(resolved_user_path)

    config_path = find_project_config(project_root)
    if config_path is not None:
        project_data = load_yaml_file(config_path)
        project_config = _parse_project_config(project_data)
        anchor = config_path.parent.resolve()
    else:
        project_config = DEFAULT_PROJECT_CONFIG
        anchor = project_root.resolve()

    effective = merge_config(
        user_config,
        project_config,
        project_root=anchor,
        project_config_path=config_path,
        user_config_path=resolved_user_path,
    )
    validate_effective_config(effective)
    return effective
