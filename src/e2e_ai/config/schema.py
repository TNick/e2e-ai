"""Configuration schema constants and known field names."""

from __future__ import annotations

PROJECT_CONFIG_NAMES: tuple[str, ...] = ("e2e-ai.yml", ".e2e-ai.yml")

USER_CONFIG_RELATIVE: tuple[str, ...] = ("e2e-ai", "config.yml")

BUILTIN_AGENT_PLUGINS: frozenset[str] = frozenset({"codex", "claude", "cursor"})

AGENT_ROLES: frozenset[str] = frozenset({"planner", "implementer", "instrumenter"})

VALID_ISOLATION_BACKENDS: frozenset[str] = frozenset(
    {
        "none",
        "docker_postgres",
        "docker_compose_postgres_template",
        "fr_two",
    }
)
