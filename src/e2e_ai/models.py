"""Core data models shared across the package.

Test identity lives in :mod:`e2e_ai.inventory` (``DiscoveredTest`` /
``build_test_id``); this module holds the value objects the runner and repair
loop pass around.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class TestStatus(StrEnum):
    """Lifecycle status of a single test in the catalog."""

    UNKNOWN = "unknown"
    PASSING = "passing"
    FAILING = "failing"
    # The failure looks like a setup/environment problem outside local control.
    BLOCKED = "blocked"
    EXCLUDED = "excluded"


@dataclass
class FailureInfo:
    """Structured context extracted from a failed test run.

    This is the payload handed to the planner agent. It intentionally mirrors
    the useful subset of Playwright's JSON reporter output.
    """

    error_message: str = ""
    stack: str = ""
    stdout_tail: str = ""
    stderr_tail: str = ""
    duration_ms: int | None = None
    attachments: list[str] = field(
        default_factory=list
    )  # trace/screenshot paths
    location: str = ""  # "file:line:col" where the failure was raised

    def is_environmental(self) -> bool:
        """Heuristic: does this failure look like a setup/env issue, not code?

        The loop uses this to decide whether continuing to spend agent effort is
        pointless. It is deliberately conservative — only very strong signals
        (connection refused, missing binaries, docker/daemon problems) count.
        """

        haystack = "\n".join(
            [self.error_message, self.stack, self.stderr_tail, self.stdout_tail]
        ).lower()
        signals = (
            "econnrefused",
            "connection refused",
            "getaddrinfo",
            "enotfound",
            "cannot connect to the docker daemon",
            "docker daemon is not running",
            "no space left on device",
            "executable doesn't exist",
            "browsertype.launch: executable",
            "please run the following command to download new browsers",
            "target closed unexpectedly during launch",
            "eacces",
            "permission denied",
        )
        return any(sig in haystack for sig in signals)
