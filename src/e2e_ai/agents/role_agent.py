"""Role-bound adapter bridging plugins to the legacy repair-loop API."""

from __future__ import annotations

from pathlib import Path

from ..config.models import EffectiveConfig
from ..errors import AgentError
from ..mcp.models import AgentMcpAttachment
from .base import AgentPlugin, AgentRunResult, LoginStatus
from .router import ROLE_TASK_CLASS, select_agent
from .schemas import ImplementRequest, InstrumentRequest, PlanRequest


class RoleBoundAgent:
    """Wrap a plugin so the loop can call ``run()`` per role."""

    def __init__(
        self,
        plugin: AgentPlugin,
        *,
        role: str,
        config: EffectiveConfig,
        profile: str | None = None,
    ) -> None:
        self._plugin = plugin
        self._role = role
        self._config = config
        self._profile = profile

    @property
    def id(self) -> str:
        return self._plugin.id

    def check_login(self) -> LoginStatus:
        return self._plugin.check_login().to_login_status()

    def run(
        self,
        prompt: str,
        *,
        workdir: Path,
        timeout: int,
        log_dir: Path | None = None,
        env: dict[str, str] | None = None,
        mcp: AgentMcpAttachment | None = None,
    ) -> AgentRunResult:
        _ = env
        if self._role == "implementer":
            request = ImplementRequest(
                prompt=prompt,
                work_dir=workdir,
                timeout_seconds=timeout,
                log_dir=log_dir,
                profile=self._profile,
                mcp=mcp,
            )
            result = self._plugin.implement(request)
        elif self._role == "instrumenter":
            request = InstrumentRequest(
                prompt=prompt,
                work_dir=workdir,
                timeout_seconds=timeout,
                log_dir=log_dir,
                profile=self._profile,
                require_schema=self._config.routing.planner_requires_schema,
                mcp=mcp,
            )
            result = self._plugin.instrument(request)
        else:
            request = PlanRequest(
                prompt=prompt,
                work_dir=workdir,
                timeout_seconds=timeout,
                log_dir=log_dir,
                profile=self._profile,
                require_schema=self._config.routing.planner_requires_schema,
                mcp=mcp,
            )
            result = self._plugin.plan(request)
        return AgentRunResult(
            agent_id=result.agent_id,
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            output_path=result.output_path,
            timed_out=result.timed_out,
            exit_class=result.exit_class,
            schema_valid=result.schema_valid,
        )


def bind_role(
    config: EffectiveConfig,
    role: str,
    plugins: dict,
    *,
    plugin_id: str | None = None,
) -> RoleBoundAgent:
    """Select and bind an agent plugin to a loop role."""

    task_class = ROLE_TASK_CLASS.get(role, "normal")
    if plugin_id is None:
        plugin = select_agent(config, role, task_class, plugins)
    else:
        plugin = plugins.get(plugin_id)
        if plugin is None:
            raise AgentError(
                f"unknown or disabled agent {plugin_id!r} for role {role!r}"
            )
    profile = None
    for agent in config.agents:
        if agent.id == role:
            profile = agent.profile
            break
    return RoleBoundAgent(plugin, role=role, config=config, profile=profile)
