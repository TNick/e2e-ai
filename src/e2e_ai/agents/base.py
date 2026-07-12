"""Agent plugin interface and shared value objects."""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .capabilities import AgentCapabilities, AgentHealth, AgentResult
from .quota import QuotaSnapshot
from .schemas import ImplementRequest, InstrumentRequest, PlanRequest


@dataclass
class LoginStatus:
    """Result of checking whether an agent is authenticated.

    ``logged_in`` is the gate the loop enforces. ``verified`` distinguishes a
    positive credential check (a token/credentials file was found, or a no-cost
    status command succeeded) from a mere "the binary exists" assumption, so the
    UI can warn when login could not be confirmed without spending tokens.
    """

    agent_id: str
    logged_in: bool
    verified: bool
    reason: str = ""

    def to_agent_health(self) -> AgentHealth:
        """Convert to the richer health record used by routing."""

        from .capabilities import QUOTA_AUTH_ERROR, QUOTA_READY

        state = QUOTA_READY if self.logged_in else QUOTA_AUTH_ERROR
        return AgentHealth(
            agent_id=self.agent_id,
            logged_in=self.logged_in,
            verified=self.verified,
            reason=self.reason,
            state=state,
        )


@dataclass
class AgentRunResult:
    """Outcome of invoking an agent on a prompt."""

    agent_id: str
    exit_code: int
    stdout: str
    stderr: str
    output_path: Path | None = None
    timed_out: bool = False
    exit_class: str | None = None
    schema_valid: bool | None = None

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


@dataclass
class AgentSpec:
    """Declarative description of a CLI-backed agent.

    Built-in agents are implemented as dedicated plugins under
    :mod:`e2e_ai.agents.plugins`. ``AgentSpec`` remains for entry-point
    compatibility and custom overrides.
    """

    id: str
    executable: str
    prompt_args: list[str] = field(default_factory=list)
    transport: str = "stdin"
    health_args: list[str] = field(default_factory=lambda: ["--version"])
    login_check_args: list[str] | None = None
    auth_files: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    profiles: dict[str, list[str]] = field(default_factory=dict)

    def merged(self, override: dict[str, Any]) -> AgentSpec:
        """Return a copy with config overrides applied."""

        data = {
            "id": self.id,
            "executable": override.get("executable", self.executable),
            "prompt_args": list(override.get("prompt_args", self.prompt_args)),
            "transport": override.get("transport", self.transport),
            "health_args": list(override.get("health_args", self.health_args)),
            "login_check_args": override.get(
                "login_check_args", self.login_check_args
            ),
            "auth_files": list(override.get("auth_files", self.auth_files)),
            "env": {**self.env, **override.get("env", {})},
            "enabled": bool(override.get("enabled", self.enabled)),
            "profiles": {**self.profiles, **override.get("profiles", {})},
        }
        return AgentSpec(**data)

    def for_profile(self, profile: str | None) -> AgentSpec:
        """Return a copy whose prompt args include the profile's extra flags."""

        if not profile or profile not in self.profiles:
            return self
        extra = self.profiles[profile]
        args = list(self.prompt_args)
        insert_at = len(args)
        if args and args[-1] in ("-p", "--print"):
            insert_at -= 1
        args[insert_at:insert_at] = list(extra)
        return AgentSpec(
            id=self.id,
            executable=self.executable,
            prompt_args=args,
            transport=self.transport,
            health_args=list(self.health_args),
            login_check_args=self.login_check_args,
            auth_files=list(self.auth_files),
            env=dict(self.env),
            enabled=self.enabled,
            profiles=dict(self.profiles),
        )


@runtime_checkable
class AgentPlugin(Protocol):
    """Common interface implemented by all agent plugins."""

    @property
    def id(self) -> str:
        """Stable identifier used in config."""

    def check_login(self) -> AgentHealth:
        """Check whether the user is logged in without requiring tokens."""

    def discover(self) -> AgentCapabilities:
        """Discover models, flags, and structured-output support."""

    def quota(self, task_class: str) -> QuotaSnapshot:
        """Return quota state for a task class."""

    def plan(self, request: PlanRequest) -> AgentResult:
        """Create a repair plan."""

    def implement(self, request: ImplementRequest) -> AgentResult:
        """Implement a repair plan."""

    def instrument(self, request: InstrumentRequest) -> AgentResult:
        """Add or recommend instrumentation for a repeated failure."""

    def supports_playwright_mcp(self) -> bool:
        """Return whether this plugin can receive a Playwright MCP server."""


class LegacyAgentRunner(abc.ABC):
    """Legacy ``run(prompt)`` interface used by the repair loop."""

    @property
    @abc.abstractmethod
    def id(self) -> str:
        """Stable identifier used in config (``planner: <id>``)."""

    @abc.abstractmethod
    def check_login(self) -> LoginStatus:
        """Report whether the agent is authenticated."""

    @abc.abstractmethod
    def run(
        self,
        prompt: str,
        *,
        workdir: Path,
        timeout: int,
        log_dir: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> AgentRunResult:
        """Run the agent against ``prompt`` inside ``workdir``."""
