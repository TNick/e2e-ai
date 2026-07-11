"""Low-level agent subprocess invocation and exit classification."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

from ..mcp.models import AgentMcpAttachment

EXIT_AUTH_ERROR = "auth_error"
EXIT_QUOTA_ERROR = "quota_error"
EXIT_MODEL_UNAVAILABLE = "model_unavailable"
EXIT_TRANSIENT_CAPACITY = "transient_capacity"
EXIT_MISCONFIGURED = "misconfigured"
EXIT_SCHEMA_FAILURE = "schema_failure"
EXIT_PERMISSION_DENIED = "permission_denied"
EXIT_TASK_FAILURE = "task_failure"
EXIT_TIMEOUT = "timeout"

_AUTH_PATTERNS = (
    re.compile(r"not\s+logged\s+in", re.I),
    re.compile(r"authentication\s+failed", re.I),
    re.compile(r"unauthorized", re.I),
    re.compile(r"invalid\s+api\s+key", re.I),
    re.compile(r"auth\s+status.*fail", re.I),
)
_QUOTA_PATTERNS = (
    re.compile(r"rate\s*limit", re.I),
    re.compile(r"quota\s+exhaust", re.I),
    re.compile(r"usage\s+limit", re.I),
    re.compile(r"too\s+many\s+requests", re.I),
    re.compile(r"429"),
)
_MODEL_PATTERNS = (
    re.compile(r"model\s+not\s+found", re.I),
    re.compile(r"model\s+unavailable", re.I),
    re.compile(r"unknown\s+model", re.I),
)
_TRANSIENT_PATTERNS = (
    re.compile(r"overloaded", re.I),
    re.compile(r"temporarily\s+unavailable", re.I),
    re.compile(r"503"),
    re.compile(r"502"),
)
_CONFIG_PATTERNS = (
    re.compile(r"unknown\s+option", re.I),
    re.compile(r"unrecognized\s+flag", re.I),
    re.compile(r"invalid\s+argument", re.I),
)


def run_agent_command(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    stdin_data: bytes | None,
    stdout_path: Path,
    stderr_path: Path,
    timeout_seconds: int,
) -> int:
    """Run an agent command and write logs."""

    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    combined = stdout_path == stderr_path
    stdout_handle = stdout_path.open("wb")
    stderr_handle = None
    try:
        if combined:
            stderr_target: int | object = subprocess.STDOUT
        else:
            stderr_handle = stderr_path.open("wb")
            stderr_target = stderr_handle
        try:
            process = subprocess.Popen(
                list(argv),
                cwd=str(cwd),
                env=dict(env),
                stdin=subprocess.PIPE if stdin_data else None,
                stdout=stdout_handle,
                stderr=stderr_target,
            )
        except (OSError, ValueError):
            stdout_handle.write(b"[e2e-ai] failed to launch agent process\n")
            return 127
        try:
            process.communicate(input=stdin_data, timeout=timeout_seconds)
            return int(process.returncode or 0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            return 124
    finally:
        stdout_handle.close()
        if stderr_handle is not None:
            stderr_handle.close()


def classify_agent_exit(
    exit_code: int,
    stdout: str,
    stderr: str,
) -> str:
    """Classify auth, quota, model, transient, config, or task failure."""

    if exit_code == 124:
        return EXIT_TIMEOUT
    text = "\n".join((stdout, stderr))
    for pattern in _AUTH_PATTERNS:
        if pattern.search(text):
            return EXIT_AUTH_ERROR
    for pattern in _QUOTA_PATTERNS:
        if pattern.search(text):
            return EXIT_QUOTA_ERROR
    for pattern in _MODEL_PATTERNS:
        if pattern.search(text):
            return EXIT_MODEL_UNAVAILABLE
    for pattern in _TRANSIENT_PATTERNS:
        if pattern.search(text):
            return EXIT_TRANSIENT_CAPACITY
    for pattern in _CONFIG_PATTERNS:
        if pattern.search(text):
            return EXIT_MISCONFIGURED
    if exit_code != 0:
        return EXIT_TASK_FAILURE
    return EXIT_TASK_FAILURE


def build_agent_invocation_environment(
    *,
    base_env: Mapping[str, str],
    mcp: AgentMcpAttachment | None,
) -> dict[str, str]:
    """Return the environment for an agent invocation."""

    env = dict(base_env)
    if mcp is None or not mcp.enabled:
        return env
    if mcp.client_config_path is not None:
        env["E2E_AI_MCP_CONFIG"] = str(mcp.client_config_path)
    env["E2E_AI_MCP_SERVER"] = mcp.server_name
    if mcp.session is not None:
        env["E2E_AI_MCP_OUTPUT_DIR"] = str(mcp.session.output_dir)
    return env


def write_agent_invocation_manifest(
    *,
    work_dir: Path,
    mcp: AgentMcpAttachment | None,
    argv: Sequence[str],
    plugin_id: str,
    mcp_version: str | None = None,
    tools_allow: Sequence[str] | None = None,
    tools_deny: Sequence[str] | None = None,
) -> Path:
    """Write agent command and MCP metadata for one invocation."""

    work_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "plugin_id": plugin_id,
        "argv": list(argv),
        "mcp_enabled": bool(mcp and mcp.enabled),
    }
    if mcp is not None:
        payload["mcp_required"] = mcp.required
        payload["mcp_degraded_reason"] = mcp.degraded_reason
        if mcp.enabled and mcp.session is not None:
            session = mcp.session
            payload.update(
                {
                    "mcp_server": mcp.server_name,
                    "mcp_config_path": (
                        str(mcp.client_config_path) if mcp.client_config_path else None
                    ),
                    "mcp_output_dir": str(session.output_dir),
                    "mcp_version": mcp_version,
                    "allowed_origins": list(session.allowed_origins),
                    "allowed_tools": list(tools_allow or ()),
                    "denied_tools": list(tools_deny or ()),
                }
            )
    path = work_dir / "agent-invocation-manifest.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path
