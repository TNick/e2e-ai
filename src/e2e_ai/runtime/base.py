"""Target runtime interface."""

from __future__ import annotations

from typing import Protocol

from .models import RuntimeContext, RuntimeState


class TargetRuntime(Protocol):
    """Lifecycle for target support services."""

    def start(self, context: RuntimeContext) -> RuntimeState:
        """Start support services and return their state."""

    def wait_until_ready(
        self, context: RuntimeContext, state: RuntimeState
    ) -> None:
        """Wait until the target runtime is ready for tests."""

    def stop(
        self,
        context: RuntimeContext,
        state: RuntimeState,
        outcome: str,
    ) -> None:
        """Stop or keep support services according to policy."""
