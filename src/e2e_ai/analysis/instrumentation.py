"""Second-pass instrumentation policy and request building.

Policy (from the plan):
  * first failure in the current run -> create a plan and implement it;
  * failure after a fix in the same run -> ask a smarter agent to
    instrument/diagnose;
  * failure after that -> create a new plan with the added context.

Escalation counts only attempts and plans from the active repair run; prior
runs still contribute to planner context but do not skip straight to
instrumentation.

Instrumentation means *temporary, reversible* diagnostic work. The implementing
agent must remove it after the fix; the acceptance gate fails if the marker
``E2E_AI_TEMP_INSTRUMENTATION`` remains in changed files.
"""

from __future__ import annotations

import sqlite3

from .failure_packet import FailurePacket

# Marker comment agents attach to temporary instrumentation edits.
TEMP_MARKER = "E2E_AI_TEMP_INSTRUMENTATION"

ALLOWED_INSTRUMENTATION = (
    "added structured logging around the failing code path",
    "added browser-console capture in a Playwright helper",
    "added request/response logging for the route under test",
    "added temporary assertions that report domain state more clearly",
    "added screenshots, traces, HAR capture, or DOM snapshots",
    "added database inspection queries in test helpers",
    "added feature-flagged debug output that is off by default",
)

DISALLOWED_INSTRUMENTATION = (
    "changing selectors or timing to make a flaky test pass",
    "broad sleeps or retries that hide the failure",
    "skipping, quarantining, or marking tests as fixme",
    "changing production behavior without a debug guard",
    "logging secrets, tokens, passwords, cookies, or private data",
    "adding permanent noisy logs without an explicit product reason",
)


def should_instrument(
    conn: sqlite3.Connection,
    test_id: str,
    failure_signature: str,
) -> bool:
    """Return whether the next pass should add instrumentation.

    True once at least one repair plan has already been implemented for this
    test (i.e. we are past the first failure) — the point at which blindly
    trying another fix is wasteful and diagnostics are worth more.
    """

    prior_plans = conn.execute(
        "SELECT COUNT(*) FROM repair_plans WHERE test_id = ?",
        (test_id,),
    ).fetchone()[0]
    return int(prior_plans) >= 1


def build_instrumentation_request(packet: FailurePacket) -> dict[str, object]:
    """Build structured instructions for a smarter (instrumentation) agent."""

    return {
        "objective": (
            "Add temporary, reversible instrumentation to prove the real root "
            "cause of this failure before any behavioral fix. Prefer test-side "
            "and debug-guarded logging over production code changes."
        ),
        "test_id": packet.test_id,
        "spec_file": packet.spec_file,
        "test_title": packet.test_title,
        "signature": packet.signature,
        "suspected_family": packet.suspected_family,
        "error_message": packet.error_message,
        "marker": TEMP_MARKER,
        "mode": "patch_file",
        "allowed": list(ALLOWED_INSTRUMENTATION),
        "disallowed": list(DISALLOWED_INSTRUMENTATION),
        # Fields the agent must return / the loop tracks for cleanup.
        "temporary_files_changed": [],
        "temporary_markers": [TEMP_MARKER],
        "cleanup_required": True,
        "permanent_observability_requested": False,
        "guidance": (
            f"Tag every temporary source change with a `{TEMP_MARKER}` "
            "comment. Do not log secrets, tokens, cookies, or private data. "
            "Do not skip the test or weaken assertions."
        ),
    }
