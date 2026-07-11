"""Task-scoped Playwright MCP for repair agents."""

from __future__ import annotations

from .artifacts import (
    list_mcp_artifacts,
    redact_mcp_artifact_text,
    summarize_mcp_artifacts,
)
from .client_configs import (
    render_claude_mcp_config,
    render_codex_mcp_config,
    render_cursor_mcp_config,
    write_agent_mcp_config,
)
from .config import DEFAULT_PLAYWRIGHT_MCP, playwright_mcp_from_effective
from .health import (
    check_node_available,
    check_npx_available,
    check_playwright_mcp_package,
    smoke_test_playwright_mcp,
)
from .models import (
    MCP_SERVER_NAME,
    AgentMcpAttachment,
    McpSessionSpec,
    PlaywrightMcpConfig,
)
from .playwright import (
    build_playwright_mcp_argv,
    resolve_npx_command,
    write_playwright_mcp_server_config,
)
from .policy import (
    build_allowed_origins,
    build_tool_allowlist,
    should_attach_playwright_mcp,
    validate_playwright_mcp_policy,
)
from .sessions import (
    cleanup_mcp_session,
    collect_mcp_artifacts,
    create_mcp_session_spec,
    prepare_agent_mcp_attachment,
)

__all__ = [
    "DEFAULT_PLAYWRIGHT_MCP",
    "MCP_SERVER_NAME",
    "AgentMcpAttachment",
    "McpSessionSpec",
    "PlaywrightMcpConfig",
    "build_allowed_origins",
    "build_playwright_mcp_argv",
    "build_tool_allowlist",
    "check_node_available",
    "check_npx_available",
    "check_playwright_mcp_package",
    "cleanup_mcp_session",
    "collect_mcp_artifacts",
    "create_mcp_session_spec",
    "list_mcp_artifacts",
    "playwright_mcp_from_effective",
    "prepare_agent_mcp_attachment",
    "redact_mcp_artifact_text",
    "render_claude_mcp_config",
    "render_codex_mcp_config",
    "render_cursor_mcp_config",
    "resolve_npx_command",
    "should_attach_playwright_mcp",
    "smoke_test_playwright_mcp",
    "summarize_mcp_artifacts",
    "validate_playwright_mcp_policy",
    "write_agent_mcp_config",
    "write_playwright_mcp_server_config",
]
