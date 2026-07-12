"""Agent role routing and preflight checks."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

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


@dataclass
class ProviderSelection:
    """Metadata for one provider selection attempt."""

    role: str
    selected_provider: str
    provider_order: tuple[str, ...] = field(default_factory=tuple)
    skipped_providers: dict[str, str] = field(default_factory=dict)
    failover_retry: bool = False


def _role_assignment(
    config: EffectiveConfig,
    role: str,
) -> AgentConfig | None:
    for agent in config.agents:
        if agent.id == role and agent.plugin is not None:
            return agent
    return None


def _role_preferences(
    config: EffectiveConfig,
    role: str,
) -> tuple[str, ...]:
    prefs = config.routing.role_preferences
    explicit = getattr(prefs, role, ())
    if explicit:
        return explicit
    assignment = _role_assignment(config, role)
    if assignment is None or not assignment.plugin:
        return ()
    primary = assignment.plugin
    preferred = ROLE_PREFER.get(role, ())
    ordered = [primary] + [pid for pid in preferred if pid != primary]
    return tuple(ordered)


def provider_pool(
    config: EffectiveConfig,
    role: str,
) -> tuple[str, ...]:
    """Return the ordered provider pool for a loop role."""

    pool = _role_preferences(config, role)
    if not pool:
        raise AgentError(f"no agent configured for role {role!r}")
    return pool


def _plugin_config(
    config: EffectiveConfig,
    plugin_id: str,
) -> AgentConfig:
    for agent in config.agents:
        if agent.id == plugin_id and agent.plugin is None:
            return agent
    return AgentConfig(id=plugin_id, enabled=True)


def configured_provider_ids(config: EffectiveConfig) -> list[str]:
    """Return distinct provider ids referenced by role pools."""

    ids: list[str] = []
    for role in ("planner", "implementer", "instrumenter"):
        for provider in provider_pool(config, role):
            if provider not in ids:
                ids.append(provider)
    return ids


def check_required_agents(
    config: EffectiveConfig,
    plugins: dict[str, AgentPlugin],
) -> list[AgentHealth]:
    """Check configured agents before a run starts."""

    checked: list[AgentHealth] = []
    seen: set[str] = set()
    for provider_id in configured_provider_ids(config):
        if provider_id in seen or provider_id not in plugins:
            continue
        seen.add(provider_id)
        checked.append(plugins[provider_id].check_login())
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
        raise AgentNotLoggedInError(
            f"the following agents are not logged in: {detail}"
        )
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
    if not getattr(plugin, "model_available", lambda: True)():
        return -500
    if not enough_quota(task_class, snapshot) and snapshot.state != "UNKNOWN":
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


def select_provider(
    config: EffectiveConfig,
    role: str,
    task_class: str,
    plugins: dict[str, AgentPlugin],
    *,
    excluded: set[str] | None = None,
    failover_retry: bool = False,
) -> ProviderSelection:
    """Select the next provider for a loop role."""

    pool = provider_pool(config, role)
    excluded = excluded or set()
    require_schema = config.routing.planner_requires_schema and role in {
        "planner",
        "instrumenter",
    }
    skipped: dict[str, str] = {}
    candidates: list[tuple[int, str, AgentPlugin]] = []

    for candidate_id in pool:
        if candidate_id in excluded:
            skipped[candidate_id] = "already failed for this repair attempt"
            continue
        plugin = plugins.get(candidate_id)
        if plugin is None:
            skipped[candidate_id] = "plugin disabled or unavailable"
            continue
        score = _score_candidate(
            plugin,
            task_class=task_class,
            require_schema=require_schema,
            routing_allow_unknown=config.routing.allow_canary,
        )
        # An unknown quota is less preferred than a verified ready provider,
        # but is still a valid failover target after a provider is exhausted.
        # Scores at or below -500 represent an unavailable login or known
        # insufficient quota and must not be invoked.
        if score <= -500:
            skipped[candidate_id] = "unhealthy or unavailable"
            continue
        candidates.append((score, candidate_id, plugin))

    candidates.sort(key=lambda item: item[0], reverse=True)
    if not candidates:
        raise AgentError(
            f"no healthy agent available for role {role!r} "
            f"(task_class={task_class})"
        )

    chosen_id = candidates[0][1]
    chosen = candidates[0][2]
    snapshot = chosen.quota(task_class)
    reserve_quota(chosen.id, task_class, snapshot)
    logger.log(
        1,
        "selected agent %s for role %s task_class=%s quota=%s "
        "failover_retry=%s",
        chosen.id,
        role,
        task_class,
        snapshot.state,
        failover_retry,
    )
    return ProviderSelection(
        role=role,
        selected_provider=chosen_id,
        provider_order=pool,
        skipped_providers=skipped,
        failover_retry=failover_retry,
    )


def select_agent(
    config: EffectiveConfig,
    role: str,
    task_class: str,
    plugins: dict[str, AgentPlugin],
) -> AgentPlugin:
    """Select an agent for planner, implementer, or instrumenter role."""

    selection = select_provider(config, role, task_class, plugins)
    plugin = plugins.get(selection.selected_provider)
    if plugin is None:
        raise AgentError(
            f"unknown or disabled agent {selection.selected_provider!r} "
            f"for role {role!r}"
        )
    return plugin


def selected_plugin_ids(config: EffectiveConfig) -> list[str]:
    """Return distinct plugin ids referenced by loop roles."""

    return configured_provider_ids(config)
