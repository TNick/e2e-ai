"""Cursor Agent CLI plugin."""

from __future__ import annotations

from ...config.models import AgentConfig, RoutingConfig
from ..capabilities import QUOTA_UNKNOWN
from ..quota import QuotaSnapshot
from ..schemas import (
    ImplementRequest,
    InstrumentRequest,
    PlanRequest,
)
from ._common import BaseCLIPlugin


def build_login_argv(executable: str) -> list[str]:
    """Build argv for ``agent status --format json``."""

    return [executable, "status", "--format", "json"]


def build_plan_argv_list(
    executable: str,
    *,
    profile_args: list[str] | None = None,
) -> list[str]:
    """Build argv for planner Cursor requests without ``--force``."""

    argv = [
        executable,
        "-p",
        "--mode",
        "plan",
        "--output-format",
        "stream-json",
    ]
    if profile_args:
        argv.extend(profile_args)
    return argv


def build_implement_argv_list(
    executable: str,
    *,
    force: bool,
    profile_args: list[str] | None = None,
) -> list[str]:
    """Build argv for implementer Cursor requests."""

    argv = [executable, "-p", "--output-format", "stream-json"]
    if force:
        argv.append("--force")
    if profile_args:
        argv.extend(profile_args)
    return argv


class CursorAgent(BaseCLIPlugin):
    """Cursor Agent CLI plugin."""

    plugin_id = "cursor"
    default_executable = "agent"
    auth_files = (
        "~/.local/share/cursor-agent/credentials.json",
        "~/.cursor/cli-config.json",
    )
    login_argv = ("status", "--format", "json")
    quota_method = "unknown"
    quota_confidence = "low"
    prompt_transport = "stdin"
    supports_mcp = True
    supports_runtime_mcp_config = True

    def _fetch_quota(self, task_class: str) -> QuotaSnapshot:
        _ = task_class
        if self.routing.allow_canary:
            return QuotaSnapshot(
                plugin_id=self.id,
                state=QUOTA_UNKNOWN,
                confidence="low",
                optimistic=True,
                detail="cursor canary enabled but not run in unit tests",
            )
        return QuotaSnapshot(
            plugin_id=self.id,
            state=QUOTA_UNKNOWN,
            confidence="low",
            optimistic=False,
            detail="cursor has no reliable individual quota preflight",
        )

    def build_plan_argv(
        self,
        request: PlanRequest,
        *,
        schema: dict[str, object] | None,
    ) -> list[str]:
        _ = schema
        return build_plan_argv_list(self.executable)

    def build_implement_argv(self, request: ImplementRequest) -> list[str]:
        return build_implement_argv_list(
            self.executable,
            force=request.isolated_workspace,
            profile_args=[],
        )

    def build_instrument_argv(
        self,
        request: InstrumentRequest,
        *,
        schema: dict[str, object] | None,
    ) -> list[str]:
        _ = schema
        return build_plan_argv_list(self.executable)


def create_cursor_agent(
    config: AgentConfig,
    routing: RoutingConfig,
) -> CursorAgent:
    """Create a Cursor plugin instance."""

    agent_config = config
    if not agent_config.id:
        agent_config = AgentConfig(
            id="cursor",
            enabled=config.enabled,
            executable=config.executable,
            profile=config.profile,
        )
    return CursorAgent(agent_config, routing)
