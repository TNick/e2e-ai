"""Claude Code agent plugin."""

from __future__ import annotations

from ...config.models import AgentConfig, RoutingConfig
from ..capabilities import QUOTA_UNKNOWN
from ..quota import QuotaSnapshot
from ..schemas import (
    ImplementRequest,
    InstrumentRequest,
    PlanRequest,
    implementation_output_schema,
    plan_output_schema,
    schema_json,
)
from ._common import BaseCLIPlugin


def build_login_argv(executable: str) -> list[str]:
    """Build argv for ``claude auth status``."""

    return [executable, "auth", "status"]


def build_plan_mode_argv(
    executable: str,
    *,
    schema: dict[str, object] | None = None,
    profile_args: list[str] | None = None,
    max_turns: int | None = None,
) -> list[str]:
    """Build argv for planning-only Claude requests."""

    argv = [
        executable,
        "-p",
        "--permission-mode",
        "plan",
        "--output-format",
        "stream-json",
    ]
    if schema is not None:
        argv.extend(["--json-schema", schema_json(schema)])
    if max_turns is not None:
        argv.extend(["--max-turns", str(max_turns)])
    if profile_args:
        argv.extend(profile_args)
    return argv


def build_implement_argv(
    executable: str,
    *,
    permission_mode: str = "dontAsk",
    schema: dict[str, object] | None = None,
    profile_args: list[str] | None = None,
    max_turns: int = 12,
) -> list[str]:
    """Build argv for implementation Claude requests."""

    argv = [
        executable,
        "-p",
        "--permission-mode",
        permission_mode,
        "--output-format",
        "stream-json",
        "--max-turns",
        str(max_turns),
    ]
    if schema is not None:
        argv.extend(["--json-schema", schema_json(schema)])
    if profile_args:
        argv.extend(profile_args)
    return argv


def _profile_args(profile: str | None) -> list[str]:
    if profile == "difficult":
        return ["--model", "opus"]
    if profile == "cheap":
        return ["--model", "haiku"]
    return []


class ClaudeAgent(BaseCLIPlugin):
    """Claude Code agent plugin."""

    plugin_id = "claude"
    default_executable = "claude"
    auth_files = (
        "~/.claude/.credentials.json",
        "~/.config/claude/.credentials.json",
    )
    login_argv = ("auth", "status")
    health_argv = ("--help",)
    quota_method = "status-line"
    quota_confidence = "low"
    prompt_transport = "stdin"
    supports_mcp = True
    supports_runtime_mcp_config = True
    supports_mcp_tool_allowlist = True
    supports_strict_mcp_config = True

    def _fetch_quota(self, task_class: str) -> QuotaSnapshot:
        _ = task_class
        return QuotaSnapshot(
            plugin_id=self.id,
            state=QUOTA_UNKNOWN,
            confidence="low",
            optimistic=not self.routing.allow_canary,
            detail="claude subscription quota observed only via status-line",
        )

    def build_plan_argv(
        self,
        request: PlanRequest,
        *,
        schema: dict[str, object] | None,
    ) -> list[str]:
        schema = schema or plan_output_schema()
        return build_plan_mode_argv(
            self.executable,
            schema=schema,
            profile_args=_profile_args(request.profile),
            max_turns=6,
        )

    def build_implement_argv(self, request: ImplementRequest) -> list[str]:
        return build_implement_argv(
            self.executable,
            schema=implementation_output_schema(),
            profile_args=_profile_args(request.profile),
        )

    def build_instrument_argv(
        self,
        request: InstrumentRequest,
        *,
        schema: dict[str, object] | None,
    ) -> list[str]:
        schema = schema or plan_output_schema()
        return build_plan_mode_argv(
            self.executable,
            schema=schema,
            profile_args=_profile_args(request.profile),
            max_turns=4,
        )


def create_claude_agent(
    config: AgentConfig,
    routing: RoutingConfig,
) -> ClaudeAgent:
    """Create a Claude plugin instance."""

    agent_config = config
    if not agent_config.id:
        agent_config = AgentConfig(
            id="claude",
            enabled=config.enabled,
            executable=config.executable,
            profile=config.profile,
        )
    return ClaudeAgent(agent_config, routing)
