"""Playwright MCP session lifecycle helpers."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from ..analysis.context import RepairContext
from ..config.models import EffectiveConfig
from ..isolation.models import EnvironmentLease
from .artifacts import list_mcp_artifacts, summarize_mcp_artifacts
from .client_configs import write_agent_mcp_config
from .models import MCP_SERVER_NAME, AgentMcpAttachment, McpSessionSpec
from .playwright import (
    build_playwright_mcp_argv,
    write_playwright_mcp_server_config,
)
from .policy import (
    build_allowed_origins,
    build_tool_allowlist,
    should_attach_playwright_mcp,
)

logger = logging.getLogger(__name__)


def create_mcp_session_spec(
    *,
    config: EffectiveConfig,
    context: RepairContext,
    lease: EnvironmentLease,
    role: str,
    agent_invocation_id: str,
) -> McpSessionSpec:
    """Create directories and runtime metadata for one MCP session."""

    mcp = config.playwright_mcp
    packet = context.packet
    output_root = (
        config.state_dir
        / "work"
        / packet.test_id
        / packet.attempt_id
        / "mcp"
        / agent_invocation_id
    )
    output_dir = output_root / "output"
    config_dir = output_root / "config"
    output_dir.mkdir(parents=True, exist_ok=True)
    config_dir.mkdir(parents=True, exist_ok=True)
    storage_path: Path | None = None
    if mcp.storage_state.mode != "none" and mcp.storage_state.path:
        src = Path(mcp.storage_state.path)
        if src.is_file():
            storage_path = output_root / "storage-state.json"
            shutil.copy2(src, storage_path)
    origins = tuple(build_allowed_origins(config=config, lease=lease))
    server_config = config_dir / "playwright-mcp.json"
    session = McpSessionSpec(
        session_id=agent_invocation_id,
        test_id=packet.test_id,
        variant_key=context.variant_key,
        attempt_id=packet.attempt_id,
        role=role,
        output_dir=output_dir,
        config_path=server_config,
        allowed_origins=origins,
        storage_state_path=storage_path,
    )
    write_playwright_mcp_server_config(mcp, session)
    return session


def prepare_agent_mcp_attachment(
    *,
    config: EffectiveConfig,
    context: RepairContext,
    lease: EnvironmentLease,
    role: str,
    plugin_id: str,
    agent_invocation_id: str,
    plugin_supports_mcp: bool,
) -> AgentMcpAttachment:
    """Create all files needed to expose MCP to one agent."""

    family = context.packet.suspected_family
    attach = should_attach_playwright_mcp(
        config=config,
        role=role,
        failure_family=family,
    )
    required = attach and bool(config.playwright_mcp.role_enabled.get(role, False))
    if not attach:
        return AgentMcpAttachment(enabled=False, required=False)
    if not plugin_supports_mcp:
        reason = f"plugin {plugin_id} does not support Playwright MCP"
        if required:
            return AgentMcpAttachment(
                enabled=False,
                required=True,
                degraded_reason=reason,
            )
        return AgentMcpAttachment(
            enabled=False,
            required=False,
            degraded_reason=reason,
        )
    try:
        session = create_mcp_session_spec(
            config=config,
            context=context,
            lease=lease,
            role=role,
            agent_invocation_id=agent_invocation_id,
        )
        client_dir = session.output_dir.parent / "client"
        attachment = AgentMcpAttachment(
            enabled=True,
            server_name=MCP_SERVER_NAME,
            session=session,
            required=required,
        )
        client_path = write_agent_mcp_config(
            plugin_id=plugin_id,
            attachment=attachment,
            target_dir=client_dir,
            argv=build_playwright_mcp_argv(config.playwright_mcp, session),
            tools_allow=build_tool_allowlist(config.playwright_mcp),
            tools_deny=config.playwright_mcp.tools_deny,
        )
        attachment = AgentMcpAttachment(
            enabled=attachment.enabled,
            server_name=attachment.server_name,
            session=attachment.session,
            client_config_path=client_path,
            required=required,
            mcp_version=config.playwright_mcp.version,
            tools_allow=tuple(build_tool_allowlist(config.playwright_mcp)),
            tools_deny=config.playwright_mcp.tools_deny,
        )
        return attachment
    except OSError as exc:
        reason = f"MCP session setup failed: {exc}"
        logger.debug("MCP setup failed", exc_info=True)
        return AgentMcpAttachment(
            enabled=False,
            required=required,
            degraded_reason=reason,
        )


def collect_mcp_artifacts(session: McpSessionSpec) -> list[Path]:
    """Return MCP artifacts produced during one agent invocation."""

    return list_mcp_artifacts(session.output_dir)


def cleanup_mcp_session(session: McpSessionSpec, keep: bool) -> None:
    """Clean or preserve a task-scoped MCP session."""

    root = session.output_dir.parent
    if keep:
        logger.log(1, "keeping MCP session artifacts at %s", root)
        return
    if root.is_dir():
        shutil.rmtree(root, ignore_errors=True)


def mcp_artifact_summary(session: McpSessionSpec) -> dict[str, object]:
    """Summarize artifacts for persistence on agent invocations."""

    paths = collect_mcp_artifacts(session)
    return summarize_mcp_artifacts(paths)
