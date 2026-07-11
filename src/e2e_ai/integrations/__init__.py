"""Project-specific integration adapters for e2e-ai.

Core e2e-ai stays project-agnostic; adapters here own the domain-specific parts
of a target project (its app stack, storage layout, database slots, and failure
families). The first adapter is :mod:`e2e_ai.integrations.fr_two`.
"""

from __future__ import annotations

__all__: list[str] = []
