"""Discovered agent capabilities and invocation results."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from attrs import define, field

if TYPE_CHECKING:
    from .base import AgentRunResult, LoginStatus

# Normalized quota states from the CLI routing guide.
QUOTA_READY = "READY"
QUOTA_DEGRADED = "DEGRADED"
QUOTA_UNKNOWN = "UNKNOWN"
QUOTA_EXHAUSTED = "EXHAUSTED"
QUOTA_AUTH_ERROR = "AUTH_ERROR"
QUOTA_MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
QUOTA_TRANSIENT_CAPACITY = "TRANSIENT_CAPACITY"
QUOTA_MISCONFIGURED = "MISCONFIGURED"


@define
class AgentCapabilities:
    """Discovered agent capabilities."""

    plugin_id: str = field()
    executable: str = field(default="")
    executable_version: str = field(default="unknown")
    output_modes: tuple[str, ...] = field(factory=tuple)
    schema_mode: bool = field(default=False)
    permission_modes: tuple[str, ...] = field(factory=tuple)
    sandbox_modes: tuple[str, ...] = field(factory=tuple)
    models: tuple[str, ...] = field(factory=tuple)
    prompt_transports: tuple[str, ...] = field(default=("stdin", "argument", "file"))
    quota_method: str = field(default="unknown")
    quota_confidence: str = field(default="low")
    supports_mcp: bool = field(default=False)
    supports_runtime_mcp_config: bool = field(default=False)
    supports_mcp_tool_allowlist: bool = field(default=False)
    supports_mcp_required_server: bool = field(default=False)
    supports_strict_mcp_config: bool = field(default=False)


@define
class AgentHealth:
    """Authentication and command availability result."""

    agent_id: str = field()
    logged_in: bool = field()
    verified: bool = field()
    reason: str = field(default="")
    state: str = field(default=QUOTA_READY)

    def to_login_status(self) -> LoginStatus:
        """Convert to the legacy login record used by the CLI."""

        from .base import LoginStatus

        return LoginStatus(
            agent_id=self.agent_id,
            logged_in=self.logged_in,
            verified=self.verified,
            reason=self.reason,
        )


@define
class AgentResult:
    """Result returned by an agent invocation."""

    agent_id: str = field()
    exit_code: int = field()
    stdout: str = field(default="")
    stderr: str = field(default="")
    exit_class: str = field(default="task_failure")
    requested_model: str | None = field(default=None)
    effective_model: str | None = field(default=None)
    requested_effort: str | None = field(default=None)
    effective_effort: str | None = field(default=None)
    output_mode: str | None = field(default=None)
    schema_valid: bool | None = field(default=None)
    quota_before: str | None = field(default=None)
    quota_after: str | None = field(default=None)
    raw_metadata: dict[str, object] = field(factory=dict)
    output_path: Path | None = field(default=None)
    timed_out: bool = field(default=False)

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def to_agent_run_result(self) -> AgentRunResult:
        """Convert to the legacy run result used by the repair loop."""

        from .base import AgentRunResult

        return AgentRunResult(
            agent_id=self.agent_id,
            exit_code=self.exit_code,
            stdout=self.stdout,
            stderr=self.stderr,
            output_path=self.output_path,
            timed_out=self.timed_out,
            exit_class=self.exit_class,
            schema_valid=self.schema_valid,
        )
