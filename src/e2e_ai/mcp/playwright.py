"""Playwright MCP command and server-config builders."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from .models import McpSessionSpec, PlaywrightMcpConfig

_NPX_CANDIDATES = ("npx", "npx.cmd", "npx.exe")


def resolve_npx_command() -> str:
    """Return the executable used to launch ``npx`` on this platform."""

    for name in _NPX_CANDIDATES:
        resolved = shutil.which(name)
        if resolved:
            return resolved
    return "npx"


def build_playwright_mcp_argv(
    config: PlaywrightMcpConfig,
    session: McpSessionSpec,
) -> list[str]:
    """Return argv for ``@playwright/mcp``."""

    package = config.package
    if not package.startswith("@"):
        package = f"@{package}"
    pinned = f"{package}@{config.version}"
    argv = [
        resolve_npx_command(),
        "-y",
        pinned,
        "--config",
        str(session.config_path),
    ]
    if config.isolated:
        argv.append("--isolated")
    if config.headless:
        argv.append("--headless")
    argv.extend(
        [
            "--output-dir",
            str(session.output_dir),
            "--output-mode",
            config.output_mode,
        ]
    )
    if config.browser:
        argv.extend(["--browser", config.browser])
    return argv


def write_playwright_mcp_server_config(
    config: PlaywrightMcpConfig,
    session: McpSessionSpec,
) -> Path:
    """Write the Playwright MCP server config for one session."""

    session.config_path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "browser": config.browser,
        "headless": config.headless,
        "isolated": config.isolated,
        "outputMode": config.output_mode,
        "outputDir": str(session.output_dir),
        "consoleLevel": config.console_level,
        "snapshotMode": config.snapshot_mode,
        "imageResponses": config.image_responses,
        "testIdAttribute": config.test_id_attribute,
        "capabilities": list(config.capabilities),
        "allowedOrigins": list(session.allowed_origins),
        "tools": {
            "allow": build_tool_allowlist(config),
            "deny": list(config.tools_deny),
        },
    }
    if session.storage_state_path is not None:
        payload["storageState"] = str(session.storage_state_path)
    session.config_path.write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )
    return session.config_path


def build_tool_allowlist(config: PlaywrightMcpConfig) -> list[str]:
    """Return allowlisted tools (re-export for playwright module consumers)."""

    from .policy import build_tool_allowlist as _build

    return _build(config)
