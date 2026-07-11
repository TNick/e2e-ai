"""Isolation backend interface."""

from __future__ import annotations

from typing import Protocol

from ..inventory.models import DiscoveredTest
from .models import EnvironmentLease, IsolationContext


class IsolationBackend(Protocol):
    """Interface for creating isolated test environments."""

    def prepare_baseline(self, context: IsolationContext) -> None:
        """Prepare shared baseline state before tests run."""

    def create_environment(
        self,
        context: IsolationContext,
        test: DiscoveredTest,
        attempt_id: str,
    ) -> EnvironmentLease:
        """Create an isolated environment for one test attempt."""

    def cleanup_environment(
        self,
        lease: EnvironmentLease,
        outcome: str,
    ) -> None:
        """Clean or keep environment state based on outcome."""
