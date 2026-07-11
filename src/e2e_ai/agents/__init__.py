"""Agent plugin system for e2e-ai.

Built-in plugins for Cursor, Codex, and Claude implement the
:class:`~e2e_ai.agents.base.AgentPlugin` protocol with runtime discovery,
quota observation, schema-constrained planning, and role-based routing.
"""

from __future__ import annotations

from .base import (
    AgentPlugin,
    AgentRunResult,
    AgentSpec,
    LegacyAgentRunner,
    LoginStatus,
)
from .capabilities import AgentCapabilities, AgentHealth, AgentResult
from .invocation import classify_agent_exit, run_agent_command
from .quota import QuotaSnapshot, enough_quota, release_quota, reserve_quota
from .registry import AgentRegistry, create_agent_plugins
from .router import check_required_agents, select_agent
from .schemas import ImplementRequest, InstrumentRequest, PlanRequest

__all__ = [
    "AgentCapabilities",
    "AgentHealth",
    "AgentPlugin",
    "AgentRegistry",
    "AgentResult",
    "AgentRunResult",
    "AgentSpec",
    "ImplementRequest",
    "InstrumentRequest",
    "LegacyAgentRunner",
    "LoginStatus",
    "PlanRequest",
    "QuotaSnapshot",
    "check_required_agents",
    "classify_agent_exit",
    "create_agent_plugins",
    "enough_quota",
    "release_quota",
    "reserve_quota",
    "run_agent_command",
    "select_agent",
]
