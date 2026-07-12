"""Agent-specific Playwright MCP client configuration rendering."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from .models import MCP_SERVER_NAME, AgentMcpAttachment


def render_codex_mcp_config(
    attachment: AgentMcpAttachment,
    *,
    argv: Sequence[str],
    tools_allow: Sequence[str],
) -> str:
    """Render Codex MCP configuration for one invocation."""

    session = attachment.session
    if session is None:
        return ""
    lines = [
        "[mcp_servers.playwright]",
        "required = true",
        f'command = "{argv[0]}"',
    ]
    if len(argv) > 1:
        args = ", ".join(f'"{part}"' for part in argv[1:])
        lines.append(f"args = [{args}]")
    if tools_allow:
        allow = ", ".join(f'"{tool}"' for tool in tools_allow)
        lines.append("[mcp_servers.playwright.tools]")
        lines.append(f"allow = [{allow}]")
    lines.append('transport = "stdio"')
    lines.append(f'output_dir = "{session.output_dir}"')
    return "\n".join(lines) + "\n"


def render_claude_mcp_config(
    attachment: AgentMcpAttachment,
    *,
    argv: Sequence[str],
    tools_allow: Sequence[str],
    tools_deny: Sequence[str],
) -> str:
    """Render Claude MCP configuration for one invocation."""

    session = attachment.session
    if session is None:
        return ""
    payload: dict[str, object] = {
        "mcpServers": {
            MCP_SERVER_NAME: {
                "command": argv[0],
                "args": list(argv[1:]),
                "transport": "stdio",
            }
        },
        "permissions": {
            "allow": list(tools_allow),
            "deny": list(tools_deny),
            "strict": True,
        },
        "outputDir": str(session.output_dir),
    }
    return json.dumps(payload, indent=2) + "\n"


def render_cursor_mcp_config(
    attachment: AgentMcpAttachment,
    *,
    argv: Sequence[str],
) -> str:
    """Render Cursor MCP configuration for one invocation."""

    session = attachment.session
    if session is None:
        return ""
    payload = {
        "mcpServers": {
            MCP_SERVER_NAME: {
                "command": argv[0],
                "args": list(argv[1:]),
            }
        }
    }
    return json.dumps(payload, indent=2) + "\n"


def write_agent_mcp_config(
    *,
    plugin_id: str,
    attachment: AgentMcpAttachment,
    target_dir: Path,
    argv: Sequence[str],
    tools_allow: Sequence[str] | None = None,
    tools_deny: Sequence[str] | None = None,
) -> Path:
    """Write plugin-specific MCP config and return its path."""

    target_dir.mkdir(parents=True, exist_ok=True)
    allow = list(tools_allow or ())
    deny = list(tools_deny or ())
    if plugin_id == "codex":
        content = render_codex_mcp_config(
            attachment, argv=argv, tools_allow=allow
        )
        path = target_dir / "codex-mcp.toml"
    elif plugin_id == "claude":
        content = render_claude_mcp_config(
            attachment,
            argv=argv,
            tools_allow=allow,
            tools_deny=deny,
        )
        path = target_dir / "claude-mcp.json"
    elif plugin_id == "cursor":
        content = render_cursor_mcp_config(attachment, argv=argv)
        path = target_dir / "cursor-mcp.json"
    else:
        content = render_claude_mcp_config(
            attachment,
            argv=argv,
            tools_allow=allow,
            tools_deny=deny,
        )
        path = target_dir / "mcp.json"
    path.write_text(content, encoding="utf-8")
    return path
