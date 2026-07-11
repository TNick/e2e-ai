"""Task-scoped Playwright MCP for repair agents.

Public names are exported lazily (PEP 562): importing this package does not
eagerly import its heavy submodules. Several of them (``artifacts`` ->
``analysis`` -> ``runner``/``isolation`` -> ``config``) would otherwise form an
import cycle with :mod:`e2e_ai.config`, which builds its default config — and
thus a :class:`PlaywrightMcpConfig` — at import time.
"""

from __future__ import annotations

# name -> submodule that defines it.
_LAZY_EXPORTS: dict[str, str] = {
    "list_mcp_artifacts": "artifacts",
    "redact_mcp_artifact_text": "artifacts",
    "summarize_mcp_artifacts": "artifacts",
    "render_claude_mcp_config": "client_configs",
    "render_codex_mcp_config": "client_configs",
    "render_cursor_mcp_config": "client_configs",
    "write_agent_mcp_config": "client_configs",
    "DEFAULT_PLAYWRIGHT_MCP": "config",
    "playwright_mcp_from_effective": "config",
    "check_node_available": "health",
    "check_npx_available": "health",
    "check_playwright_mcp_package": "health",
    "smoke_test_playwright_mcp": "health",
    "MCP_SERVER_NAME": "models",
    "AgentMcpAttachment": "models",
    "McpSessionSpec": "models",
    "PlaywrightMcpConfig": "models",
    "build_playwright_mcp_argv": "playwright",
    "resolve_npx_command": "playwright",
    "write_playwright_mcp_server_config": "playwright",
    "build_allowed_origins": "policy",
    "build_tool_allowlist": "policy",
    "should_attach_playwright_mcp": "policy",
    "validate_playwright_mcp_policy": "policy",
    "cleanup_mcp_session": "sessions",
    "collect_mcp_artifacts": "sessions",
    "create_mcp_session_spec": "sessions",
    "prepare_agent_mcp_attachment": "sessions",
}

__all__ = sorted(_LAZY_EXPORTS)


def __getattr__(name: str) -> object:  # PEP 562
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module = importlib.import_module(f".{module_name}", __name__)
    return getattr(module, name)


def __dir__() -> list[str]:
    return sorted(set(__all__) | set(globals()))
