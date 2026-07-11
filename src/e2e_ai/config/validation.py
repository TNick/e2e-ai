"""Configuration validation helpers."""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence

from ..errors import ConfigError
from .models import CommandSpec, EffectiveConfig
from .schema import BUILTIN_AGENT_PLUGINS, VALID_ISOLATION_BACKENDS
from .target import (
    VALID_TARGET_SCOPES,
    has_editable_backend,
    has_editable_frontend,
    path_within_root,
    resolve_surface_path,
)

logger = logging.getLogger(__name__)


def validate_command_spec(command: CommandSpec, label: str) -> None:
    """Validate an argv-based command."""

    if not command.argv:
        raise ConfigError(f"{label} must include at least one argv entry")
    if any(not isinstance(part, str) or not part.strip() for part in command.argv):
        raise ConfigError(f"{label} argv entries must be non-empty strings")


def validate_exclude_patterns(patterns: Sequence[str]) -> None:
    """Validate test exclude patterns."""

    for index, pattern in enumerate(patterns):
        if not isinstance(pattern, str) or not pattern.strip():
            raise ConfigError(
                f"exclude pattern at index {index} must be a non-empty string"
            )
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ConfigError(
                f"exclude pattern at index {index} is not valid regex: {exc}"
            ) from exc


def validate_target_config(config: EffectiveConfig) -> None:
    """Validate declared edit surfaces and scope rules."""

    target = config.target
    if target.scope not in VALID_TARGET_SCOPES:
        allowed = ", ".join(sorted(VALID_TARGET_SCOPES))
        raise ConfigError(
            f"invalid target.scope {target.scope!r}; expected one of: {allowed}"
        )
    if not target.surfaces:
        raise ConfigError("target.surfaces must not be empty")

    root = config.project_root.resolve()
    for name, surface in target.surfaces.items():
        resolved = resolve_surface_path(root, surface.path)
        if surface.editable and not path_within_root(resolved, root):
            raise ConfigError(
                f"editable target surface {name!r} path "
                f"{surface.path!r} resolves outside project root"
            )

    if not has_editable_frontend(target):
        raise ConfigError("target requires an editable frontend surface")

    if target.scope == "frontend_only":
        if has_editable_backend(target):
            raise ConfigError(
                "frontend_only scope cannot include an editable backend surface"
            )

    if target.scope == "full_stack":
        if not has_editable_backend(target):
            raise ConfigError("full_stack scope requires an editable backend surface")

    if target.scope == "frontend_with_backend_reference":
        backend = target.surfaces.get("backend")
        if backend is None:
            raise ConfigError(
                "frontend_with_backend_reference scope requires a backend surface"
            )
        if backend.editable:
            raise ConfigError(
                "frontend_with_backend_reference backend surface must be "
                "read-only (editable: false)"
            )
        if backend.role and backend.role != "reference":
            logger.log(
                1,
                "backend surface role %r is not 'reference' for scope %s",
                backend.role,
                target.scope,
            )


def validate_effective_config(config: EffectiveConfig) -> None:
    """Raise ConfigError when config is invalid."""

    if not config.project_id.strip():
        raise ConfigError("project.id must not be empty")

    if config.playwright.list_command is None:
        raise ConfigError("playwright.list_command is required")
    if config.playwright.run_command is None:
        raise ConfigError("playwright.run_command is required")

    validate_command_spec(
        config.playwright.list_command,
        "playwright.list_command",
    )
    validate_command_spec(
        config.playwright.run_command,
        "playwright.run_command",
    )

    if config.full_verification is not None and config.full_verification.command:
        validate_command_spec(
            config.full_verification.command,
            "full_verification.command",
        )

    validate_exclude_patterns(config.exclude)
    validate_target_config(config)

    seen_ids: set[str] = set()
    for agent in config.agents:
        if agent.id in seen_ids:
            raise ConfigError(f"duplicate agent id {agent.id!r}")
        seen_ids.add(agent.id)
        if agent.plugin is not None and agent.plugin not in BUILTIN_AGENT_PLUGINS:
            raise ConfigError(
                f"unknown agent plugin {agent.plugin!r} for {agent.id!r}; "
                f"expected one of: {', '.join(sorted(BUILTIN_AGENT_PLUGINS))}"
            )

    if config.repair_policy.max_attempts_per_test < 0:
        raise ConfigError("repair_policy.max_attempts_per_test must not be negative")
    if config.repair_policy.max_same_signature_attempts < 1:
        raise ConfigError(
            "repair_policy.max_same_signature_attempts must be at least 1"
        )
    if config.repair_policy.max_agent_seconds < 1:
        raise ConfigError("repair_policy.max_agent_seconds must be at least 1")

    # Imported lazily to avoid a config -> mcp -> analysis -> runner -> config
    # import cycle at config-package import time.
    from ..mcp.policy import validate_playwright_mcp_policy

    validate_playwright_mcp_policy(
        config.playwright_mcp,
        state_dir=config.state_dir,
    )

    if config.isolation.backend not in VALID_ISOLATION_BACKENDS:
        raise ConfigError(
            f"invalid isolation backend {config.isolation.backend!r}; "
            f"expected one of: {', '.join(sorted(VALID_ISOLATION_BACKENDS))}"
        )

    if config.isolation.refresh_template not in {"auto", "always", "never"}:
        raise ConfigError(
            "isolation.refresh_template must be one of: auto, always, never"
        )

    if config.routing.long_task_min_remaining_percent < 0:
        raise ConfigError(
            "routing.long_task_min_remaining_percent must not be negative"
        )
    if config.routing.schema_retry_limit < 0:
        raise ConfigError("routing.schema_retry_limit must not be negative")
    if config.routing.canary_cache_seconds < 0:
        raise ConfigError("routing.canary_cache_seconds must not be negative")

    logger.log(1, "validated effective config for project %s", config.project_id)
