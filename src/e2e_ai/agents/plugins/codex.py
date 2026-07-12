"""Codex CLI agent plugin."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from ...config.models import AgentConfig, RoutingConfig
from ...mcp.models import AgentMcpAttachment
from ..capabilities import QUOTA_READY, AgentResult
from ..quota import QuotaSnapshot
from ..schemas import (
    ImplementRequest,
    InstrumentRequest,
    PlanRequest,
    implementation_output_schema,
    plan_output_schema,
)
from ._common import BaseCLIPlugin, invoke_argv, write_schema_file


def build_login_argv(executable: str) -> list[str]:
    """Build argv for ``codex login status``."""

    return [executable, "login", "status"]


def build_exec_argv(
    executable: str,
    *,
    sandbox: str,
    schema_path: Path | None = None,
    approval: str | None = None,
    profile_args: list[str] | None = None,
    mcp_profile: str | None = None,
) -> list[str]:
    """Build argv for ``codex exec`` with sandbox and optional schema file."""

    if os.name == "nt" and sandbox == "workspace-write":
        sandbox = "danger-full-access"

    argv = [executable, "exec", "--json", "--ignore-user-config"]
    if schema_path is not None:
        argv.extend(["--output-schema", str(schema_path)])
    argv.extend(["--sandbox", sandbox])
    # Codex accepts approval policy only as a config override on ``exec``,
    # not via the global ``--ask-for-approval`` flag after the subcommand.
    if approval is not None:
        argv.extend(["-c", f"approval_policy={approval}"])
    if mcp_profile is not None:
        argv.extend(["-p", mcp_profile])
    if profile_args:
        argv.extend(profile_args)
    return argv


def prepare_codex_mcp_runtime(
    mcp: AgentMcpAttachment | None,
    *,
    log_dir: Path | None,
    env: dict[str, str],
) -> tuple[dict[str, str], str | None, list[Path]]:
    """Return env overrides and profile name for one Playwright MCP session."""

    if mcp is None or not mcp.enabled or mcp.client_config_path is None:
        return env, None, []
    if log_dir is None:
        return env, None, []

    session_id = mcp.session.session_id if mcp.session is not None else "mcp"
    runtime_home = log_dir / f"codex-home-{session_id}"
    runtime_home.mkdir(parents=True, exist_ok=True)
    cleanup_paths = [runtime_home]

    real_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    auth_src = real_home / "auth.json"
    auth_dst = runtime_home / "auth.json"
    if auth_src.is_file() and not auth_dst.exists():
        shutil.copy2(auth_src, auth_dst)

    profile_name = f"e2e-ai-mcp-{session_id.replace('_', '-')}"
    profile_dst = runtime_home / f"{profile_name}.config.toml"
    shutil.copy2(mcp.client_config_path, profile_dst)

    run_env = {**env, "CODEX_HOME": str(runtime_home)}
    return run_env, profile_name, cleanup_paths


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
    prompt_transport = "stdin"
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

    def _codex_exec(
        self,
        request: PlanRequest | ImplementRequest | InstrumentRequest,
        *,
        sandbox: str,
        schema: dict[str, object] | None,
        approval: str | None,
        quota_task: str,
    ) -> AgentResult:
        """Run Codex with optional schema written to a temporary file."""

        schema_path = write_schema_file(schema) if schema is not None else None
        cleanup = [schema_path] if schema_path is not None else None
        mcp = getattr(request, "mcp", None)
        run_env, mcp_profile, mcp_cleanup = prepare_codex_mcp_runtime(
            mcp,
            log_dir=request.log_dir,
            env=self._run_env(),
        )
        if cleanup is None:
            cleanup = list(mcp_cleanup)
        else:
            cleanup = [*cleanup, *mcp_cleanup]
        argv = build_exec_argv(
            self.executable,
            sandbox=sandbox,
            schema_path=schema_path,
            approval=approval,
            profile_args=_profile_args(request.profile),
            mcp_profile=mcp_profile,
        )
        snap = self.quota(quota_task)
        return invoke_argv(
            self.id,
            argv,
            cwd=request.work_dir,
            prompt=request.prompt,
            transport=self.prompt_transport,
            env=run_env,
            timeout_seconds=request.timeout_seconds,
            log_dir=request.log_dir,
            quota_before=snap.state,
            cleanup_paths=cleanup,
            **self._mcp_invoke_kwargs(request),
        )

    def plan(self, request: PlanRequest) -> AgentResult:
        schema = plan_output_schema() if request.require_schema else None
        return self._codex_exec(
            request,
            sandbox="read-only",
            schema=schema,
            approval=None,
            quota_task="difficult",
        )

    def implement(self, request: ImplementRequest) -> AgentResult:
        return self._codex_exec(
            request,
            sandbox="workspace-write",
            schema=implementation_output_schema(),
            approval="never",
            quota_task="normal",
        )

    def instrument(self, request: InstrumentRequest) -> AgentResult:
        schema = plan_output_schema() if request.require_schema else None
        return self._codex_exec(
            request,
            sandbox="workspace-write",
            schema=schema,
            approval="never",
            quota_task="difficult",
        )

    def build_plan_argv(
        self,
        request: PlanRequest,
        *,
        schema: dict[str, object] | None,
    ) -> list[str]:
        schema = schema or plan_output_schema()
        schema_path = write_schema_file(schema)
        return build_exec_argv(
            self.executable,
            sandbox="read-only",
            schema_path=schema_path,
            profile_args=_profile_args(request.profile),
        )

    def build_implement_argv(self, request: ImplementRequest) -> list[str]:
        schema_path = write_schema_file(implementation_output_schema())
        return build_exec_argv(
            self.executable,
            sandbox="workspace-write",
            schema_path=schema_path,
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
        schema_path = write_schema_file(schema)
        return build_exec_argv(
            self.executable,
            sandbox="workspace-write",
            schema_path=schema_path,
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
