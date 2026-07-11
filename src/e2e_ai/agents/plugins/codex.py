"""Codex CLI agent plugin."""

from __future__ import annotations

from ...config.models import AgentConfig, RoutingConfig
from ..capabilities import QUOTA_READY
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
    """Build argv for ``codex login status``."""

    return [executable, "login", "status"]


def build_exec_argv(
    executable: str,
    *,
    sandbox: str,
    schema: dict[str, object] | None = None,
    approval: str | None = None,
    profile_args: list[str] | None = None,
) -> list[str]:
    """Build argv for ``codex exec`` with sandbox and optional schema."""

    argv = [executable, "exec", "--json"]
    if schema is not None:
        argv.extend(["--output-schema", schema_json(schema)])
    argv.extend(["--sandbox", sandbox])
    if approval is not None:
        argv.extend(["--ask-for-approval", approval])
    if profile_args:
        argv.extend(profile_args)
    return argv


def _profile_args(profile: str | None) -> list[str]:
    if profile == "difficult":
        return ["-c", "model_reasoning_effort=high"]
    if profile == "cheap":
        return ["-c", "model_reasoning_effort=low"]
    return []


class CodexAgent(BaseCLIPlugin):
    """Codex CLI agent plugin."""

    plugin_id = "codex"
    default_executable = "codex"
    auth_files = ("~/.codex/auth.json",)
    login_argv = ("login", "status")
    quota_method = "app-server"
    quota_confidence = "high"
    prompt_transport = "argument"
    supports_mcp = True
    supports_runtime_mcp_config = True
    supports_mcp_tool_allowlist = True
    supports_mcp_required_server = True

    def _fetch_quota(self, task_class: str) -> QuotaSnapshot:
        _ = task_class
        health = self.check_login()
        if not health.logged_in:
            return QuotaSnapshot(
                plugin_id=self.id,
                state=health.state,
                confidence="high",
                detail=health.reason,
            )
        return QuotaSnapshot(
            plugin_id=self.id,
            state=QUOTA_READY,
            confidence="medium",
            detail="codex app-server quota preflight deferred",
        )

    def build_plan_argv(
        self,
        request: PlanRequest,
        *,
        schema: dict[str, object] | None,
    ) -> list[str]:
        schema = schema or plan_output_schema()
        return build_exec_argv(
            self.executable,
            sandbox="read-only",
            schema=schema,
            profile_args=_profile_args(request.profile),
        )

    def build_implement_argv(self, request: ImplementRequest) -> list[str]:
        return build_exec_argv(
            self.executable,
            sandbox="workspace-write",
            schema=implementation_output_schema(),
            approval="never",
            profile_args=_profile_args(request.profile),
        )

    def build_instrument_argv(
        self,
        request: InstrumentRequest,
        *,
        schema: dict[str, object] | None,
    ) -> list[str]:
        schema = schema or plan_output_schema()
        return build_exec_argv(
            self.executable,
            sandbox="workspace-write",
            schema=schema,
            approval="never",
            profile_args=_profile_args(request.profile),
        )


def create_codex_agent(
    config: AgentConfig,
    routing: RoutingConfig,
) -> CodexAgent:
    """Create a Codex plugin instance."""

    agent_config = config
    if not agent_config.id:
        agent_config = AgentConfig(
            id="codex",
            enabled=config.enabled,
            executable=config.executable,
            profile=config.profile,
        )
    return CodexAgent(agent_config, routing)
