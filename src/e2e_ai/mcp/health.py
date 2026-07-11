"""Health checks for Playwright MCP dependencies."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from ..agents.capabilities import QUOTA_MISCONFIGURED, QUOTA_READY, AgentHealth
from .models import PlaywrightMcpConfig
from .playwright import resolve_npx_command

logger = logging.getLogger(__name__)


def check_node_available() -> AgentHealth:
    """Return whether Node.js is available for Playwright MCP."""

    node = shutil.which("node") or shutil.which("node.exe")
    if node is None:
        return AgentHealth(
            agent_id="playwright-mcp",
            logged_in=False,
            verified=False,
            reason="node executable not found on PATH",
            state=QUOTA_MISCONFIGURED,
        )
    try:
        result = subprocess.run(
            [node, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.debug("node --version failed", exc_info=True)
        return AgentHealth(
            agent_id="playwright-mcp",
            logged_in=False,
            verified=False,
            reason=f"node --version failed: {exc}",
            state=QUOTA_MISCONFIGURED,
        )
    if result.returncode != 0:
        return AgentHealth(
            agent_id="playwright-mcp",
            logged_in=False,
            verified=False,
            reason=f"node --version exited {result.returncode}",
            state=QUOTA_MISCONFIGURED,
        )
    version = (result.stdout or result.stderr).strip().splitlines()[0]
    return AgentHealth(
        agent_id="playwright-mcp",
        logged_in=True,
        verified=True,
        reason=f"node {version}",
        state=QUOTA_READY,
    )


def check_npx_available() -> AgentHealth:
    """Return whether ``npx`` can be executed."""

    npx = resolve_npx_command()
    if shutil.which(npx) is None and npx == "npx":
        return AgentHealth(
            agent_id="playwright-mcp",
            logged_in=False,
            verified=False,
            reason="npx executable not found on PATH",
            state=QUOTA_MISCONFIGURED,
        )
    try:
        result = subprocess.run(
            [npx, "--version"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.debug("npx --version failed", exc_info=True)
        return AgentHealth(
            agent_id="playwright-mcp",
            logged_in=False,
            verified=False,
            reason=f"npx --version failed: {exc}",
            state=QUOTA_MISCONFIGURED,
        )
    if result.returncode != 0:
        return AgentHealth(
            agent_id="playwright-mcp",
            logged_in=False,
            verified=False,
            reason=f"npx --version exited {result.returncode}",
            state=QUOTA_MISCONFIGURED,
        )
    version = (result.stdout or result.stderr).strip().splitlines()[0]
    return AgentHealth(
        agent_id="playwright-mcp",
        logged_in=True,
        verified=True,
        reason=f"npx {version}",
        state=QUOTA_READY,
    )


def check_playwright_mcp_package(
    config: PlaywrightMcpConfig,
) -> AgentHealth:
    """Return whether the pinned MCP package can start."""

    if not config.enabled:
        return AgentHealth(
            agent_id="playwright-mcp",
            logged_in=True,
            verified=True,
            reason="playwright MCP disabled",
            state=QUOTA_READY,
        )
    package = config.package
    if not package.startswith("@"):
        package = f"@{package}"
    pinned = f"{package}@{config.version}"
    argv = [resolve_npx_command(), "-y", pinned, "--help"]
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.debug("playwright mcp --help failed", exc_info=True)
        return AgentHealth(
            agent_id="playwright-mcp",
            logged_in=False,
            verified=False,
            reason=f"could not run {pinned} --help: {exc}",
            state=QUOTA_MISCONFIGURED,
        )
    text = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0 and "browser" not in text.lower():
        return AgentHealth(
            agent_id="playwright-mcp",
            logged_in=False,
            verified=False,
            reason=f"{pinned} --help exited {result.returncode}",
            state=QUOTA_MISCONFIGURED,
        )
    return AgentHealth(
        agent_id="playwright-mcp",
        logged_in=True,
        verified=True,
        reason=f"package {pinned} responds to --help",
        state=QUOTA_READY,
    )


def smoke_test_playwright_mcp(
    config: PlaywrightMcpConfig,
    work_dir: Path,
) -> AgentHealth:
    """Run a minimal MCP startup smoke test."""

    if not config.enabled:
        return AgentHealth(
            agent_id="playwright-mcp",
            logged_in=True,
            verified=True,
            reason="playwright MCP disabled",
            state=QUOTA_READY,
        )
    work_dir.mkdir(parents=True, exist_ok=True)
    node = check_node_available()
    if not node.logged_in:
        return node
    npx = check_npx_available()
    if not npx.logged_in:
        return npx
    return check_playwright_mcp_package(config)
