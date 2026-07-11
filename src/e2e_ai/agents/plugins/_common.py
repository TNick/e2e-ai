"""Shared plugin execution helpers."""

from __future__ import annotations

import logging
import os
import tempfile
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

from ...config.models import AgentConfig, RoutingConfig
from ...mcp.models import AgentMcpAttachment
from ..capabilities import AgentCapabilities, AgentHealth, AgentResult
from ..health import health_from_probe, run_probe
from ..invocation import (
    build_agent_invocation_environment,
    classify_agent_exit,
    run_agent_command,
    write_agent_invocation_manifest,
)
from ..quota import QuotaSnapshot, invalidate_quota_cache
from ..schemas import (
    ImplementRequest,
    InstrumentRequest,
    PlanRequest,
    plan_output_schema,
    schema_json,
)

logger = logging.getLogger(__name__)

_CAPABILITIES_TTL_SECONDS = 60.0


def resolve_executable(config: AgentConfig, default: str) -> str:
    """Return the configured executable or default name."""

    return config.executable or default


def write_prompt_file(prompt: str) -> Path:
    """Write a prompt to a temporary file for file-based transport."""

    fd, name = tempfile.mkstemp(prefix="e2e-ai-prompt-", suffix=".md")
    os.close(fd)
    path = Path(name)
    path.write_text(prompt, encoding="utf-8")
    return path


def invoke_argv(
    agent_id: str,
    argv: Sequence[str],
    *,
    cwd: Path,
    prompt: str,
    transport: str,
    env: Mapping[str, str],
    timeout_seconds: int,
    log_dir: Path | None,
    quota_before: str | None = None,
    mcp: AgentMcpAttachment | None = None,
    mcp_version: str | None = None,
    tools_allow: Sequence[str] | None = None,
    tools_deny: Sequence[str] | None = None,
) -> AgentResult:
    """Run one argv list and return a normalized :class:`AgentResult`."""

    command = list(argv)
    stdin_data: bytes | None = None
    tmp_file: Path | None = None
    if transport == "argument":
        command.append(prompt)
    elif transport == "file":
        tmp_file = write_prompt_file(prompt)
        command.append(str(tmp_file))
    else:
        stdin_data = prompt.encode("utf-8")

    run_env = build_agent_invocation_environment(base_env=env, mcp=mcp)
    manifest_dir = log_dir or (cwd / ".e2e-ai-agent")
    write_agent_invocation_manifest(
        work_dir=manifest_dir,
        mcp=mcp,
        argv=command,
        plugin_id=agent_id,
        mcp_version=mcp_version,
        tools_allow=tools_allow,
        tools_deny=tools_deny,
    )

    output_path: Path | None = None
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        output_path = log_dir / (f"{agent_id}-{stamp}.log")

    stdout_path = output_path or (cwd / ".e2e-ai-agent-stdout.log")
    stderr_path = stdout_path
    exit_code = run_agent_command(
        command,
        cwd=cwd,
        env=run_env,
        stdin_data=stdin_data,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        timeout_seconds=timeout_seconds,
    )
    if tmp_file is not None:
        tmp_file.unlink(missing_ok=True)

    text = ""
    if stdout_path.is_file():
        text = stdout_path.read_text(encoding="utf-8", errors="replace")
    timed_out = exit_code == 124
    stdout, stderr = text, ""
    if "--- stderr ---" in text:
        stdout, stderr = text.split("--- stderr ---", 1)
    exit_class = classify_agent_exit(exit_code, stdout, stderr)
    invalidate_quota_cache()
    return AgentResult(
        agent_id=agent_id,
        exit_code=exit_code,
        stdout=stdout.strip(),
        stderr=stderr.strip(),
        exit_class=exit_class,
        output_path=output_path,
        timed_out=timed_out,
        quota_before=quota_before,
        quota_after=quota_before,
    )


class BaseCLIPlugin:
    """Common behavior for built-in CLI agent plugins."""

    plugin_id: str = ""
    default_executable: str = ""
    auth_files: tuple[str, ...] = ()
    login_argv: tuple[str, ...] | None = None
    health_argv: tuple[str, ...] = ("--version",)
    prompt_transport: str = "stdin"
    supports_mcp: bool = False
    supports_runtime_mcp_config: bool = False
    supports_mcp_tool_allowlist: bool = False
    supports_mcp_required_server: bool = False
    supports_strict_mcp_config: bool = False

    def __init__(
        self,
        config: AgentConfig,
        routing: RoutingConfig,
    ) -> None:
        self.config = config
        self.routing = routing
        self._capabilities_cache: tuple[float, AgentCapabilities] | None = None
        self._quota_cache: tuple[float, QuotaSnapshot] | None = None

    @property
    def id(self) -> str:
        return self.config.id or self.plugin_id

    @property
    def executable(self) -> str:
        return resolve_executable(self.config, self.default_executable)

    def _run_env(self) -> dict[str, str]:
        return {**os.environ, "PYTHONIOENCODING": "utf-8"}

    def check_login(self) -> AgentHealth:
        return health_from_probe(
            self.id,
            executable=self.executable,
            auth_files=self.auth_files,
            login_argv=self.login_argv,
            health_argv=self.health_argv,
        )

    def discover(self) -> AgentCapabilities:
        now = time.monotonic()
        if (
            self._capabilities_cache is not None
            and (now - self._capabilities_cache[0]) < _CAPABILITIES_TTL_SECONDS
        ):
            return self._capabilities_cache[1]
        version = "unknown"
        ok, out = run_probe(self.executable, self.health_argv)
        if ok and out:
            version = out.splitlines()[0][:120]
        caps = AgentCapabilities(
            plugin_id=self.id,
            executable=self.executable,
            executable_version=version,
            output_modes=("text", "json", "stream-json"),
            schema_mode=True,
            prompt_transports=(self.prompt_transport, "file", "stdin"),
            quota_method=getattr(self, "quota_method", "unknown"),
            quota_confidence=getattr(self, "quota_confidence", "low"),
            supports_mcp=self.supports_mcp,
            supports_runtime_mcp_config=self.supports_runtime_mcp_config,
            supports_mcp_tool_allowlist=self.supports_mcp_tool_allowlist,
            supports_mcp_required_server=self.supports_mcp_required_server,
            supports_strict_mcp_config=self.supports_strict_mcp_config,
        )
        self._capabilities_cache = (now, caps)
        return caps

    def quota(self, task_class: str) -> QuotaSnapshot:
        _ = task_class
        now = time.monotonic()
        if (
            self._quota_cache is not None
            and (now - self._quota_cache[0]) < self.routing.canary_cache_seconds
        ):
            return self._quota_cache[1]
        snapshot = self._fetch_quota(task_class)
        self._quota_cache = (now, snapshot)
        return snapshot

    def _fetch_quota(self, task_class: str) -> QuotaSnapshot:
        _ = task_class
        return QuotaSnapshot(plugin_id=self.id)

    def supports_playwright_mcp(self) -> bool:
        """Return whether this plugin can receive Playwright MCP."""

        return self.supports_mcp

    def _mcp_invoke_kwargs(self, request) -> dict[str, object]:
        mcp = getattr(request, "mcp", None)
        kwargs: dict[str, object] = {"mcp": mcp}
        if mcp is not None and mcp.enabled:
            kwargs["tools_allow"] = mcp.tools_allow
            kwargs["tools_deny"] = mcp.tools_deny
            kwargs["mcp_version"] = mcp.mcp_version
        return kwargs

    def plan(self, request: PlanRequest) -> AgentResult:
        argv = self.build_plan_argv(
            request,
            schema=plan_output_schema() if request.require_schema else None,
        )
        snap = self.quota("difficult")
        return invoke_argv(
            self.id,
            argv,
            cwd=request.work_dir,
            prompt=request.prompt,
            transport=self.prompt_transport,
            env=self._run_env(),
            timeout_seconds=request.timeout_seconds,
            log_dir=request.log_dir,
            quota_before=snap.state,
            **self._mcp_invoke_kwargs(request),
        )

    def implement(self, request: ImplementRequest) -> AgentResult:
        argv = self.build_implement_argv(request)
        snap = self.quota("normal")
        return invoke_argv(
            self.id,
            argv,
            cwd=request.work_dir,
            prompt=request.prompt,
            transport=self.prompt_transport,
            env=self._run_env(),
            timeout_seconds=request.timeout_seconds,
            log_dir=request.log_dir,
            quota_before=snap.state,
            **self._mcp_invoke_kwargs(request),
        )

    def instrument(self, request: InstrumentRequest) -> AgentResult:
        argv = self.build_instrument_argv(
            request,
            schema=plan_output_schema() if request.require_schema else None,
        )
        snap = self.quota("difficult")
        return invoke_argv(
            self.id,
            argv,
            cwd=request.work_dir,
            prompt=request.prompt,
            transport=self.prompt_transport,
            env=self._run_env(),
            timeout_seconds=request.timeout_seconds,
            log_dir=request.log_dir,
            quota_before=snap.state,
            **self._mcp_invoke_kwargs(request),
        )

    def build_plan_argv(
        self,
        request: PlanRequest,
        *,
        schema: dict[str, object] | None,
    ) -> list[str]:
        raise NotImplementedError

    def build_implement_argv(self, request: ImplementRequest) -> list[str]:
        raise NotImplementedError

    def build_instrument_argv(
        self,
        request: InstrumentRequest,
        *,
        schema: dict[str, object] | None,
    ) -> list[str]:
        return self.build_plan_argv(
            PlanRequest(
                prompt=request.prompt,
                work_dir=request.work_dir,
                timeout_seconds=request.timeout_seconds,
                log_dir=request.log_dir,
                profile=request.profile,
                require_schema=request.require_schema,
            ),
            schema=schema,
        )

    def schema_args(self, schema: dict[str, object] | None) -> list[str]:
        if schema is None:
            return []
        return ["--output-schema", schema_json(schema)]
