"""Default Playwright MCP configuration helpers."""

from __future__ import annotations

from ..config.models import EffectiveConfig
from .models import PlaywrightMcpConfig

DEFAULT_PLAYWRIGHT_MCP = PlaywrightMcpConfig()


def playwright_mcp_from_effective(
    config: EffectiveConfig,
) -> PlaywrightMcpConfig:
    """Return the merged Playwright MCP config for a run."""

    return config.playwright_mcp
