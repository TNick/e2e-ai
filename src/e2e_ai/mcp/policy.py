"""Playwright MCP attachment policy and validation."""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlparse

from ..config.models import EffectiveConfig
from ..errors import ConfigError
from ..isolation.models import EnvironmentLease
from .models import DEFAULT_MCP_TOOLS_DENY, PlaywrightMcpConfig

logger = logging.getLogger(__name__)

_BROWSER_FAMILIES = frozenset(
    {
        "locator",
        "navigation",
        "timeout",
        "visibility",
        "ui",
    }
)


def should_attach_playwright_mcp(
    *,
    config: EffectiveConfig,
    role: str,
    failure_family: str | None,
) -> bool:
    """Return whether a repair agent should receive Playwright MCP."""

    mcp = config.playwright_mcp
    if not mcp.enabled:
        return False
    role_on = bool(mcp.role_enabled.get(role, False))
    if role_on:
        return True
    if role == "planner" and failure_family in _BROWSER_FAMILIES:
        return True
    return False


def _origin_from_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url.strip())
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def build_allowed_origins(
    *,
    config: EffectiveConfig,
    lease: EnvironmentLease,
) -> list[str]:
    """Return origins allowed for one MCP browser task."""

    mcp = config.playwright_mcp
    origins: list[str] = []
    if mcp.origins.from_environment_lease:
        for url in (lease.frontend_url, lease.backend_url):
            origin = _origin_from_url(url)
            if origin and origin not in origins:
                origins.append(origin)
    for extra in mcp.origins.extra_allow:
        origin = _origin_from_url(extra) or extra.strip()
        if origin and origin not in origins:
            origins.append(origin)
    return origins


def build_tool_allowlist(config: PlaywrightMcpConfig) -> list[str]:
    """Return the MCP tools an agent may use."""

    denied = set(config.tools_deny)
    return [tool for tool in config.tools_allow if tool not in denied]


def validate_playwright_mcp_policy(
    config: PlaywrightMcpConfig,
    *,
    state_dir: Path | None = None,
    require_origins: bool = False,
) -> None:
    """Validate the MCP policy and reject unsafe defaults."""

    if not config.enabled:
        return
    version = (config.version or "").strip().lower()
    if not version or version == "latest":
        raise ConfigError(
            "playwright_mcp.version must be a pinned version, not empty or 'latest'"
        )
    if config.unrestricted_file_access:
        raise ConfigError(
            "playwright_mcp.unrestricted_file_access is unsafe; set "
            "playwright_mcp.allow_unsafe_file_access: true to opt in explicitly"
        )
    allow = set(config.tools_allow)
    for unsafe in DEFAULT_MCP_TOOLS_DENY:
        if unsafe in allow:
            raise ConfigError(
                f"playwright_mcp.tools.allow must not include unsafe tool {unsafe!r}"
            )
    if state_dir is not None:
        probe = state_dir / "work" / ".mcp-probe"
        try:
            probe.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ConfigError(
                f"playwright_mcp enabled but state work dir is not writable: {exc}"
            ) from exc
    if require_origins and config.origins.from_environment_lease:
        logger.log(
            1,
            "playwright_mcp origins come from environment lease at runtime",
        )
