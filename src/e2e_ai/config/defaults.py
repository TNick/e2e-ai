"""Built-in configuration defaults."""

from __future__ import annotations

from .models import (
    AgentConfig,
    PlaywrightConfig,
    ProjectConfig,
    RepairPolicy,
    RoutingConfig,
    UserConfig,
)

DEFAULT_USER_CONFIG = UserConfig(
    agents=(
        AgentConfig(id="codex", enabled=True, executable="codex"),
        AgentConfig(
            id="claude", enabled=True, executable="claude", max_turns=40
        ),
        AgentConfig(id="cursor", enabled=True, executable="agent"),
    ),
    routing=RoutingConfig(),
)

DEFAULT_PROJECT_CONFIG = ProjectConfig(
    repair_policy=RepairPolicy(),
    playwright=PlaywrightConfig(),
)

DEFAULT_USER_CONFIG_YAML = """\
# e2e-ai user defaults (merged under project config).
agents:
  codex:
    enabled: true
    executable: codex
  claude:
    enabled: true
    executable: claude
    max_turns: 40
  cursor:
    enabled: true
    executable: agent

routing:
  allow_canary: false
  long_task_min_remaining_percent: 25
  role_preferences:
    planner: [codex, claude, cursor]
    implementer: [claude, codex, cursor]
    instrumenter: [cursor, claude, codex]
  failover:
    enabled: true
    max_switches_per_test: 6
"""

DEFAULT_PROJECT_CONFIG_YAML = """\
# e2e-ai project configuration.
project:
  id: my-project

state:
  dir: .e2e-ai

target:
  scope: frontend_only
  surfaces:
    frontend:
      path: .
      editable: true
      role: source

playwright:
  cwd: e2e
  list_command:
    - pnpm
    - exec
    - playwright
    - test
    - --list
  run_command:
    - pnpm
    - exec
    - playwright
    - test

exclude:
  tests: []

isolation:
  backend: none

agents:
  planner:
    plugin: codex
    profile: difficult
  implementer:
    plugin: codex
    profile: cheap
"""
