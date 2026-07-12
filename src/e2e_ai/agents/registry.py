"""Agent discovery, configuration, and role resolution."""

from __future__ import annotations

import logging
from collections.abc import Callable
from importlib import metadata
from typing import Any

from ..config import AgentConfig, EffectiveConfig
from ..errors import AgentError
from .base import AgentPlugin, AgentSpec, LoginStatus
from .builtins import BUILTIN_SPECS
from .cli_agent import CLIAgent
from .plugins.claude import create_claude_agent
from .plugins.codex import create_codex_agent
from .plugins.cursor import create_cursor_agent
from .role_agent import RoleBoundAgent, bind_role
from .router import (
    require_required_agents,
    selected_plugin_ids,
)

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "e2e_ai.agents"

AgentFactory = Callable[[AgentConfig, "EffectiveConfig"], AgentPlugin]

BUILTIN_AGENT_FACTORIES: dict[str, AgentFactory] = {
    "codex": lambda cfg, effective: create_codex_agent(cfg, effective.routing),
    "claude": lambda cfg, effective: create_claude_agent(
        cfg, effective.routing
    ),
    "cursor": lambda cfg, effective: create_cursor_agent(
        cfg, effective.routing
    ),
}

_EXTRA_PLUGINS: dict[str, AgentPlugin] = {}


def load_entry_point_plugins() -> dict[str, AgentPlugin]:
    """Load external agent plugins from package entry points."""

    plugins: dict[str, AgentPlugin] = {}
    try:
        entry_points = metadata.entry_points(group=ENTRY_POINT_GROUP)
    except TypeError:  # pragma: no cover - Python <3.10 compat
        entry_points = metadata.entry_points().get(ENTRY_POINT_GROUP, [])  # type: ignore

    for ep in entry_points:
        if ep.name in BUILTIN_AGENT_FACTORIES:
            continue
        try:
            obj = ep.load()
        except Exception as exc:  # pragma: no cover - defensive
            raise AgentError(
                f"failed to load agent plugin {ep.name!r}: {exc}"
            ) from exc
        if isinstance(obj, AgentPlugin):
            plugins[obj.id] = obj
        elif isinstance(obj, type) and issubclass(obj, AgentPlugin):
            inst = obj()
            plugins[inst.id] = inst
    return plugins


def _discover_specs() -> dict[str, AgentSpec]:
    """Return built-in specs plus any contributed by installed plugins."""

    specs: dict[str, AgentSpec] = dict(BUILTIN_SPECS)
    plugins = load_entry_point_plugins()
    _EXTRA_PLUGINS.clear()
    _EXTRA_PLUGINS.update(plugins)
    return specs


def _agent_entry(config: EffectiveConfig, agent_id: str) -> AgentConfig | None:
    for agent in config.agents:
        if agent.id == agent_id:
            return agent
    return None


def _plugin_overrides(config: EffectiveConfig) -> dict[str, dict[str, Any]]:
    overrides: dict[str, dict[str, Any]] = {}
    for agent in config.agents:
        if agent.plugin is not None:
            continue
        entry: dict[str, Any] = {"enabled": agent.enabled}
        if agent.executable is not None:
            entry["executable"] = agent.executable
        overrides[agent.id] = entry
    return overrides


def create_agent_plugins(config: EffectiveConfig) -> dict[str, AgentPlugin]:
    """Instantiate configured agent plugins."""

    specs = _discover_specs()
    overrides = _plugin_overrides(config)
    plugins: dict[str, AgentPlugin] = dict(_EXTRA_PLUGINS)
    variant_agents: list[AgentConfig] = []

    for agent in config.agents:
        if agent.provider is None:
            continue
        if agent.provider not in BUILTIN_AGENT_FACTORIES:
            continue
        if agent.id in BUILTIN_AGENT_FACTORIES:
            continue
        if not agent.enabled:
            continue
        variant_agents.append(agent)

    for plugin_id, factory in BUILTIN_AGENT_FACTORIES.items():
        override = overrides.get(plugin_id, {})
        if override.get("enabled") is False:
            continue
        agent_cfg = AgentConfig(
            id=plugin_id,
            enabled=bool(override.get("enabled", True)),
            executable=override.get("executable"),
        )
        plugins[plugin_id] = factory(agent_cfg, config)

    for agent in variant_agents:
        factory = BUILTIN_AGENT_FACTORIES[agent.provider]
        base_override = overrides.get(agent.provider, {})
        agent_cfg = AgentConfig(
            id=agent.id,
            provider=agent.provider,
            enabled=agent.enabled,
            executable=agent.executable or base_override.get("executable"),
            model_candidates=agent.model_candidates,
            reasoning_effort=agent.reasoning_effort,
        )
        plugins[agent.id] = factory(agent_cfg, config)

    for agent_id, spec in specs.items():
        if agent_id in BUILTIN_AGENT_FACTORIES:
            continue
        override = overrides.get(agent_id, {}) or {}
        merged = spec.merged(override)
        if not merged.enabled:
            plugins.pop(agent_id, None)
            continue
        plugins[agent_id] = _spec_plugin_adapter(merged, config)

    logger.log(1, "created %d agent plugin(s)", len(plugins))
    return plugins


def _spec_plugin_adapter(
    spec: AgentSpec, config: EffectiveConfig
) -> AgentPlugin:
    """Wrap a legacy :class:`CLIAgent` as an :class:`AgentPlugin`."""

    cli = CLIAgent(spec)

    class SpecPluginAdapter:
        @property
        def id(self) -> str:
            return cli.id

        def check_login(self):
            return cli.check_login().to_agent_health()

        def discover(self):
            from .capabilities import AgentCapabilities

            return AgentCapabilities(
                plugin_id=cli.id,
                executable=spec.executable,
                schema_mode=False,
                quota_method="unknown",
            )

        def quota(self, task_class: str):
            from .capabilities import QUOTA_UNKNOWN
            from .quota import QuotaSnapshot

            _ = task_class
            return QuotaSnapshot(plugin_id=cli.id, state=QUOTA_UNKNOWN)

        def plan(self, request):
            return _legacy_run(cli, request.prompt, request)

        def implement(self, request):
            return _legacy_run(cli, request.prompt, request)

        def instrument(self, request):
            return _legacy_run(cli, request.prompt, request)

        def supports_playwright_mcp(self) -> bool:
            return False

    return SpecPluginAdapter()  # type: ignore[return-value]


def _legacy_run(cli: CLIAgent, prompt: str, request) -> Any:
    from .capabilities import AgentResult
    from .invocation import classify_agent_exit

    run = cli.run(
        prompt,
        workdir=request.work_dir,
        timeout=request.timeout_seconds,
        log_dir=request.log_dir,
    )
    return AgentResult(
        agent_id=run.agent_id,
        exit_code=run.exit_code,
        stdout=run.stdout,
        stderr=run.stderr,
        exit_class=classify_agent_exit(run.exit_code, run.stdout, run.stderr),
        output_path=run.output_path,
        timed_out=run.timed_out,
    )


class AgentRegistry:
    """Holds configured agents and resolves loop roles to them."""

    def __init__(
        self,
        plugins: dict[str, AgentPlugin],
        config: EffectiveConfig,
    ) -> None:
        self._plugins = plugins
        self._config = config

    @classmethod
    def from_config(cls, config: EffectiveConfig) -> AgentRegistry:
        plugins = create_agent_plugins(config)
        return cls(plugins, config)

    def get(self, agent_id: str) -> RoleBoundAgent:
        try:
            plugin = self._plugins[agent_id]
        except KeyError:
            available = ", ".join(sorted(self._plugins)) or "(none)"
            raise AgentError(
                f"unknown or disabled agent {agent_id!r}; "
                f"available: {available}"
            ) from None
        return RoleBoundAgent(plugin, role="implementer", config=self._config)

    def role(self, role: str) -> RoleBoundAgent:
        """Return the routed agent assigned to a loop role."""

        return bind_role(self._config, role, self._plugins)

    def selected_ids(self) -> list[str]:
        return selected_plugin_ids(self._config)

    def check_logins(
        self, agent_ids: list[str] | None = None
    ) -> list[LoginStatus]:
        ids = agent_ids if agent_ids is not None else self.selected_ids()
        return [
            self._plugins[agent_id].check_login().to_login_status()
            for agent_id in ids
            if agent_id in self._plugins
        ]

    def require_logins(
        self, agent_ids: list[str] | None = None
    ) -> list[LoginStatus]:
        if agent_ids is not None:
            statuses = self.check_logins(agent_ids)
            bad = [s for s in statuses if not s.logged_in]
            if bad:
                detail = "; ".join(f"{s.agent_id}: {s.reason}" for s in bad)
                from ..errors import AgentNotLoggedInError

                raise AgentNotLoggedInError(
                    f"the following agents are not logged in: {detail}"
                )
            return statuses
        health = require_required_agents(self._config, self._plugins)
        return [h.to_login_status() for h in health]
