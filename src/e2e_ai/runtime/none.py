"""No-op target runtime for projects without Docker startup."""

from __future__ import annotations

from attrs import define

from ..config.models import EffectiveConfig
from .models import RuntimeContext, RuntimeState


@define
class NoTargetRuntime:
    """Target runtime that does not start support services."""

    def start(self, context: RuntimeContext) -> RuntimeState:
        work_dir = context.state_dir / "runs" / context.run_id / "runtime"
        work_dir.mkdir(parents=True, exist_ok=True)
        return RuntimeState(
            id="none",
            backend="none",
            work_dir=work_dir,
            started=False,
            healthy=True,
        )

    def wait_until_ready(
        self,
        context: RuntimeContext,
        state: RuntimeState,
    ) -> None:
        _ = (context, state)

    def stop(
        self,
        context: RuntimeContext,
        state: RuntimeState,
        outcome: str,
    ) -> None:
        _ = (context, state, outcome)


def create_no_target_runtime(config: EffectiveConfig) -> NoTargetRuntime:
    """Return a no-op target runtime."""

    _ = config
    return NoTargetRuntime()
