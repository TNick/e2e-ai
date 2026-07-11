"""No-op isolation backend for already-running target apps."""

from __future__ import annotations

import os

from attrs import define, field

from ..config.models import EffectiveConfig
from ..inventory.models import DiscoveredTest
from .base import IsolationBackend
from .models import EnvironmentLease, IsolationContext


def _passthrough_env(context: IsolationContext) -> dict[str, str]:
    env = {str(k): str(v) for k, v in context.env.items()}
    pw = context.config.playwright
    for env_name in (pw.base_url_env, pw.api_base_env, pw.lab_flag_env):
        if env_name and env_name in os.environ:
            env[env_name] = os.environ[env_name]
    return env


@define
class NoIsolationBackend:
    """Isolation backend that uses an already-running target app."""

    config: EffectiveConfig = field()
    _context: IsolationContext | None = field(default=None, init=False)

    def prepare_baseline(self, context: IsolationContext) -> None:
        """No shared baseline is required."""

        self._context = context

    def create_environment(
        self,
        context: IsolationContext,
        test: DiscoveredTest,
        attempt_id: str,
    ) -> EnvironmentLease:
        """Return configured environment values without provisioning."""

        self._context = context
        work_dir = context.state_dir / "work" / test.id / attempt_id
        work_dir.mkdir(parents=True, exist_ok=True)
        env = _passthrough_env(context)
        frontend = env.get(context.config.playwright.base_url_env or "")
        backend = env.get(context.config.playwright.api_base_env or "")
        return EnvironmentLease(
            id=f"none-{test.id}-{attempt_id}",
            test_id=test.id,
            work_dir=work_dir,
            env=env,
            frontend_url=frontend or None,
            backend_url=backend or None,
        )

    def cleanup_environment(
        self,
        lease: EnvironmentLease,
        outcome: str,
    ) -> None:
        """Nothing to clean up for external stacks."""

        _ = lease
        _ = outcome


def create_no_isolation_backend(config: EffectiveConfig) -> IsolationBackend:
    """Return a no-op isolation backend."""

    return NoIsolationBackend(config=config)
