"""Agent role routing and preflight checks."""

from __future__ import annotations

import logging

from ..config.models import AgentConfig, EffectiveConfig
from ..errors import AgentError, AgentNotLoggedInError
from .base import AgentPlugin
from .capabilities import AgentHealth
from .quota import enough_quota, reserve_quota

logger = logging.getLogger(__name__)

ROLE_TASK_CLASS = {
    "planner": "difficult",
    "implementer": "normal",
    "instrumenter": "difficult",
}

ROLE_PREFER = {
    "planner": ("codex", "claude", "cursor"),
    "implementer": ("codex", "cursor", "claude"),
    "instrumenter": ("codex", "claude", "cursor"),
}


def _role_assignment(
    config: EffectiveConfig,
    role: str,
) -> AgentConfig | None:
    for agent in config.agents:
        if agent.id == role and agent.plugin is not None:
            return agent
    return None


def _plugin_config(
    config: EffectiveConfig,
    plugin_id: str,
) -> AgentConfig:
    for agent in config.agents:
        if agent.id == plugin_id and agent.plugin is None:
            return agent
    return AgentConfig(id=plugin_id, enabled=True)


def check_required_agents(
    config: EffectiveConfig,
    plugins: dict[str, AgentPlugin],
) -> list[AgentHealth]:
    """Check configured agents before a run starts."""

    checked: list[AgentHealth] = []
    seen: set[str] = set()
    for role in ("planner", "implementer", "instrumenter"):
        assignment = _role_assignment(config, role)
        if assignment is None or not assignment.plugin:
            continue
        plugin_id = assignment.plugin
        if plugin_id in seen or plugin_id not in plugins:
            continue
        seen.add(plugin_id)
        checked.append(plugins[plugin_id].check_login())
    return checked


def require_required_agents(
    config: EffectiveConfig,
    plugins: dict[str, AgentPlugin],
) -> list[AgentHealth]:
    """Check logins and raise when a selected plugin is not authenticated."""

    statuses = check_required_agents(config, plugins)
    bad = [status for status in statuses if not status.logged_in]
    if bad:
        detail = "; ".join(f"{s.agent_id}: {s.reason}" for s in bad)
        raise AgentNotLoggedInError(f"the following agents are not logged in: {detail}")
    return statuses


def _score_candidate(
    plugin: AgentPlugin,
    *,
    task_class: str,
    require_schema: bool,
    routing_allow_unknown: bool,
) -> int:
    caps = plugin.discover()
    snapshot = plugin.quota(task_class)
    score = 0
    if not plugin.check_login().logged_in:
        return -1000
    if not enough_quota(task_class, snapshot):
        return -500
    if snapshot.state == "UNKNOWN" and not routing_allow_unknown:
        score -= 100
    if require_schema and not caps.schema_mode:
        score -= 200
    if snapshot.confidence == "high":
        score += 20
    elif snapshot.confidence == "medium":
        score += 10
    return score


def select_agent(
    config: EffectiveConfig,
    role: str,
    task_class: str,
    plugins: dict[str, AgentPlugin],
) -> AgentPlugin:
    """Select an agent for planner, implementer, or instrumenter role."""

    assignment = _role_assignment(config, role)
    if assignment is None or not assignment.plugin:
        raise AgentError(f"no agent configured for role {role!r}")

    plugin_id = assignment.plugin
    if plugin_id not in plugins:
        raise AgentError(f"unknown or disabled agent {plugin_id!r} for role {role!r}")

    require_schema = config.routing.planner_requires_schema and role in {
        "planner",
        "instrumenter",
    }
    preferred = ROLE_PREFER.get(role, ())
    candidates: list[tuple[int, str, AgentPlugin]] = []
    ordered = [plugin_id] + [pid for pid in preferred if pid != plugin_id]
    for candidate_id in ordered:
        plugin = plugins.get(candidate_id)
        if plugin is None:
            continue
        score = _score_candidate(
            plugin,
            task_class=task_class,
            require_schema=require_schema,
            routing_allow_unknown=config.routing.allow_canary,
        )
        candidates.append((score, candidate_id, plugin))

    candidates.sort(key=lambda item: item[0], reverse=True)
    if not candidates or candidates[0][0] < 0:
        raise AgentError(
            f"no healthy agent available for role {role!r} (task_class={task_class})"
        )

    chosen = candidates[0][2]
    snapshot = chosen.quota(task_class)
    reserve_quota(chosen.id, task_class, snapshot)
    logger.log(
        1,
        "selected agent %s for role %s task_class=%s quota=%s",
        chosen.id,
        role,
        task_class,
        snapshot.state,
    )
    return chosen


def selected_plugin_ids(config: EffectiveConfig) -> list[str]:
    """Return distinct plugin ids referenced by loop roles."""

    ids: list[str] = []
    for role in ("planner", "implementer", "instrumenter"):
        assignment = _role_assignment(config, role)
        if assignment and assignment.plugin and assignment.plugin not in ids:
            ids.append(assignment.plugin)
    return ids
