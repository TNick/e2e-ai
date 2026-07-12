"""Repair-loop policy: external blockers, attempt limits, instrumentation."""

from __future__ import annotations

import sqlite3

from ..analysis.failure_packet import FailurePacket
from ..config import EffectiveConfig

_DOCKER_BACKENDS = frozenset(
    {
        "docker_postgres",
        "docker_compose_postgres_template",
        "fr_two",
    }
)

_EXTERNAL_SIGNALS = (
    "please run the following command to download new browsers",
    "browsertype.launch: executable",
    "executable doesn't exist",
    "cannot connect to the docker daemon",
    "docker daemon is not running",
    "error during connect: this error may indicate that the docker daemon",
    "unauthorized: authentication required",
    "npm err! code e401",
    "npm err! 401",
    "private registry",
    "registry credentials",
    "not logged in",
    "authentication failed",
    "quota exhausted",
    "rate limit",
    "insufficient_quota",
    "billing hard limit",
    "all configured agents are unauthenticated",
    "no implementation agent available",
)


def _haystack(packet: FailurePacket) -> str:
    parts = [
        packet.error_message,
        packet.stack,
        packet.stdout_tail,
        packet.stderr_tail,
    ]
    return "\n".join(parts).lower()


def classify_external_blocker(
    packet: FailurePacket,
    *,
    config: EffectiveConfig | None = None,
) -> bool:
    """Return whether a failure appears outside local code control.

    Conservative: product assertion failures and agent fix failures are never
    external blockers by themselves.
    """

    text = _haystack(packet)

    # Strong product-failure signals — never external on their own.
    product_signals = (
        "expect(",
        "assertion",
        "locator",
        "timeout",
        "timed out",
        "tohave",
        "tobevisible",
        "expected:",
        "received:",
    )
    if any(sig in text for sig in product_signals):
        return False

    if any(sig in text for sig in _EXTERNAL_SIGNALS):
        return True

    if config is not None:
        backend = config.isolation.backend
        if backend in _DOCKER_BACKENDS:
            docker_signals = (
                "econnrefused",
                "connection refused",
                "cannot connect to the docker daemon",
                "docker daemon is not running",
                "error response from daemon",
            )
            if any(sig in text for sig in docker_signals):
                return True

    return False


def should_stop_test(
    conn: sqlite3.Connection,
    test_id: str,
    max_attempts: int,
    *,
    run_id: str | None = None,
) -> bool:
    """Return whether the test exhausted local repair attempts."""

    if max_attempts <= 0:
        return False
    run_filter = ""
    params: tuple[object, ...] = (test_id,)
    if run_id is not None:
        run_filter = " AND run_id = ?"
        params = (test_id, run_id)
    row = conn.execute(
        f"""
        SELECT COUNT(*) FROM attempts
        WHERE test_id = ? AND status != 'passed'
        {run_filter}
        """,
        params,
    ).fetchone()
    failed_runs = int(row[0]) if row is not None else 0
    return failed_runs >= max_attempts


def should_escalate_to_instrumentation(
    conn: sqlite3.Connection,
    test_id: str,
    signature: str,
    *,
    run_id: str,
    max_same_signature: int = 2,
) -> bool:
    """Return whether repeated failure in this run needs instrumentation.

    Escalation requires at least one repair plan from the current run (a fix
    was already attempted) and the same failure signature recurring enough
    times within that run. History from earlier runs does not count.
    """

    prior_plans = conn.execute(
        """
        SELECT COUNT(*) FROM repair_plans rp
        JOIN failure_packets fp ON fp.id = rp.failure_packet_id
        JOIN attempts a ON a.id = fp.attempt_id
        WHERE rp.test_id = ? AND a.run_id = ?
        """,
        (test_id, run_id),
    ).fetchone()[0]
    if int(prior_plans) < 1:
        return False

    same_sig = conn.execute(
        """
        SELECT COUNT(*) FROM failure_packets fp
        JOIN attempts a ON a.id = fp.attempt_id
        WHERE a.test_id = ? AND fp.signature = ? AND a.run_id = ?
        """,
        (test_id, signature, run_id),
    ).fetchone()[0]
    return int(same_sig) >= max_same_signature
