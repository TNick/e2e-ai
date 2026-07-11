"""Exception hierarchy for e2e-ai."""

from __future__ import annotations


class E2eAiError(Exception):
    """Base class for all e2e-ai errors."""


class ConfigError(E2eAiError):
    """Raised when configuration is missing or invalid."""


class AgentError(E2eAiError):
    """Raised when an agent backend is misconfigured or unavailable."""


class AgentNotLoggedInError(AgentError):
    """Raised when a selected agent is not authenticated.

    The loop refuses to start when a selected agent cannot prove it is logged
    in, because unattended runs would otherwise burn time on prompts that the
    CLI silently rejects.
    """


class DockerError(E2eAiError):
    """Raised when a Docker/database operation fails."""


class CatalogError(E2eAiError):
    """Raised when the Playwright catalog cannot be built."""
