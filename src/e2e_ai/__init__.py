"""e2e-ai: an AI-driven loop that runs Playwright e2e tests and fixes failures.

The package orchestrates a sequential run of a project's Playwright test suite.
When a test fails, a *planner* agent is asked to produce a fix plan, a cheaper
*implementer* agent applies it, and the test is re-run. Tests that keep failing
are escalated to a smarter *instrumentation* agent. The loop only stops when the
whole suite is green or the remaining failures look like environment/setup
problems that are outside local control.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
