"""Configuration loading, merging, and validation for e2e-ai."""

from __future__ import annotations

from .defaults import (
    DEFAULT_PROJECT_CONFIG,
    DEFAULT_PROJECT_CONFIG_YAML,
    DEFAULT_USER_CONFIG,
    DEFAULT_USER_CONFIG_YAML,
)
from .loader import (
    default_user_config_path,
    ensure_user_config,
    find_project_config,
    load_effective_config,
    load_project_config,
    load_user_config,
    load_yaml_file,
    merge_config,
)

# Import the typed models FIRST so leaf modules (runner, isolation, …) that do
# ``from ..config import EffectiveConfig`` resolve even while ``.defaults``
# construction triggers the mcp/analysis/runner import graph.
from .models import (
    AgentConfig,
    CommandSpec,
    EffectiveConfig,
    FullVerificationConfig,
    IsolationConfig,
    MonitorConfig,
    PlaywrightConfig,
    PlaywrightReportEnv,
    PostgresIsolationConfig,
    ProjectConfig,
    RepairPolicy,
    RoutingConfig,
    TargetConfig,
    TargetSurfaceConfig,
    UserConfig,
    default_target_config,
)
from .schema import (
    AGENT_ROLES,
    BUILTIN_AGENT_PLUGINS,
    PROJECT_CONFIG_NAMES,
    VALID_ISOLATION_BACKENDS,
)
from .validation import (
    validate_command_spec,
    validate_effective_config,
    validate_exclude_patterns,
)

__all__ = [
    "AGENT_ROLES",
    "AgentConfig",
    "BUILTIN_AGENT_PLUGINS",
    "CommandSpec",
    "DEFAULT_PROJECT_CONFIG",
    "DEFAULT_PROJECT_CONFIG_YAML",
    "DEFAULT_USER_CONFIG",
    "DEFAULT_USER_CONFIG_YAML",
    "EffectiveConfig",
    "FullVerificationConfig",
    "IsolationConfig",
    "MonitorConfig",
    "PROJECT_CONFIG_NAMES",
    "PlaywrightConfig",
    "PlaywrightReportEnv",
    "PostgresIsolationConfig",
    "ProjectConfig",
    "RepairPolicy",
    "RoutingConfig",
    "TargetConfig",
    "TargetSurfaceConfig",
    "UserConfig",
    "VALID_ISOLATION_BACKENDS",
    "default_user_config_path",
    "default_target_config",
    "ensure_user_config",
    "find_project_config",
    "load_effective_config",
    "load_project_config",
    "load_user_config",
    "load_yaml_file",
    "merge_config",
    "validate_command_spec",
    "validate_effective_config",
    "validate_exclude_patterns",
]
