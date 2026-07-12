"""Assemble previous-failure and previous-plan context for planning agents.

Prompt *construction* belongs to a later step; this module only prepares the
structured, size-bounded evidence and explicitly labels prior plans as FAILED.
"""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING

from attrs import define, evolve, field

from .failure_packet import FailurePacket
from .store import _packet_from_payload

if TYPE_CHECKING:
    from ..config.models import EffectiveConfig
    from ..inventory.models import DiscoveredTest

# Deterministic context budgets (see the plan's context config answers).
PREVIOUS_PLAN_LIMIT = 3
PREVIOUS_FAILURE_LIMIT = 5
MAX_CONTEXT_CHARS = 30_000

# Header the prompt builder must keep so agents never mistake prior plans for
# working solutions — a specific requirement from the initial ideas.
FAILED_PLANS_HEADER = "PREVIOUS FAILED PLANS (these did NOT fix the test)"


@define
class RepairContext:
    """Context passed to planning and implementation agents."""

    packet: FailurePacket = field()
    previous_failures: tuple[FailurePacket, ...] = field(factory=tuple)
    previous_plans: tuple[str, ...] = field(factory=tuple)
    failed_plans_header: str = field(default=FAILED_PLANS_HEADER)
    omitted: tuple[str, ...] = field(factory=tuple)
    logical_key: str = field(default="")
    variant_key: str = field(default="")
    project_name: str | None = field(default=None)
    test_list_selector: str = field(default="")
    mcp_recommended: bool = field(default=False)
    mcp_reason: str | None = field(default=None)


def build_logical_key(spec_file: str, title: str) -> str:
    """Return a durable logical test key (file + title, no line metadata)."""

    return "{}::{}".format(spec_file.replace("\\", "/"), title)


def build_variant_key(
    spec_file: str,
    title: str,
    project_name: str | None,
) -> str:
    """Return a durable runnable variant key including browser project."""

    return "{}::{}::{}".format(
        project_name or "default",
        spec_file.replace("\\", "/"),
        title,
    )


def build_test_list_selector(
    *,
    spec_file: str,
    title: str,
    project_name: str | None,
    raw_list_line: str = "",
) -> str:
    """Return the Playwright list selector agents should use for reruns."""

    if raw_list_line.strip():
        return raw_list_line.strip()
    if project_name:
        return f"[{project_name}] › {spec_file} › {title}"
    return f"{spec_file} › {title}"


def _mcp_recommendation(
    config: EffectiveConfig | None,
    failure_family: str | None,
) -> tuple[bool, str | None]:
    if config is None or not config.playwright_mcp.enabled:
        return False, None
    from ..mcp.policy import should_attach_playwright_mcp

    for role in ("instrumenter", "implementer", "planner"):
        if should_attach_playwright_mcp(
            config=config,
            role=role,
            failure_family=failure_family,
        ):
            return True, f"playwright_mcp enabled for role {role}"
    return False, None


def load_previous_failures(
    conn: sqlite3.Connection,
    test_id: str,
    limit: int = PREVIOUS_FAILURE_LIMIT,
) -> list[FailurePacket]:
    """Return recent failure packets for this test, newest first."""

    rows = conn.execute(
        """
        SELECT fp.payload_json
        FROM failure_packets fp
        JOIN attempts a ON a.id = fp.attempt_id
        WHERE a.test_id = ?
        ORDER BY fp.created_at DESC
        LIMIT ?
        """,
        (test_id, limit),
    ).fetchall()
    packets: list[FailurePacket] = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"])
        except (json.JSONDecodeError, TypeError):
            continue
        packets.append(_packet_from_payload(payload))
    return packets


def load_previous_plans(
    conn: sqlite3.Connection,
    test_id: str,
    limit: int = PREVIOUS_FAILURE_LIMIT,
) -> list[str]:
    """Return previous plans for this test, oldest first (chronological)."""

    rows = conn.execute(
        """
        SELECT plan_text FROM repair_plans
        WHERE test_id = ?
        ORDER BY created_at ASC, rowid ASC
        LIMIT ?
        """,
        (test_id, limit),
    ).fetchall()
    return [str(row["plan_text"]) for row in rows]


def _enrich_flake_evidence(
    conn: sqlite3.Connection,
    packet: FailurePacket,
) -> FailurePacket:
    """Fill pass/fail history and repeat-signature flags on the packet."""

    passes = conn.execute(
        "SELECT COUNT(*) FROM attempts WHERE test_id = ? AND status = 'passed'",
        (packet.test_id,),
    ).fetchone()[0]
    fails = conn.execute(
        "SELECT COUNT(*) FROM attempts WHERE test_id = ? "
        "AND status != 'passed'",
        (packet.test_id,),
    ).fetchone()[0]
    repeat = conn.execute(
        """
        SELECT COUNT(*) FROM failure_packets fp
        JOIN attempts a ON a.id = fp.attempt_id
        WHERE a.test_id = ? AND fp.signature = ? AND fp.id != ?
        """,
        (packet.test_id, packet.signature, packet.id),
    ).fetchone()[0]
    evidence = None
    if int(passes) > 0 and int(fails) > 0:
        evidence = (
            f"test has both passed ({passes}) and failed ({fails}) before; "
            "treat as potentially flaky — look for race conditions or missing "
            "wait conditions rather than assuming a hard regression"
        )
    return evolve(
        packet,
        is_repeat_signature=int(repeat) > 0,
        previous_pass_count=int(passes),
        previous_fail_count=int(fails),
        flake_evidence=evidence,
    )


def build_repair_context(
    conn: sqlite3.Connection,
    packet: FailurePacket,
    *,
    test: DiscoveredTest | None = None,
    config: EffectiveConfig | None = None,
) -> RepairContext:
    """Build context for a planning agent from run history."""

    enriched = _enrich_flake_evidence(conn, packet)
    previous_failures = tuple(
        p
        for p in load_previous_failures(
            conn, packet.test_id, PREVIOUS_FAILURE_LIMIT
        )
        if p.attempt_id != packet.attempt_id
    )
    previous_plans = tuple(
        load_previous_plans(conn, packet.test_id, PREVIOUS_PLAN_LIMIT)
    )
    spec_file = test.spec_file if test is not None else packet.spec_file
    title = test.title if test is not None else packet.test_title
    project_name = test.project_name if test is not None else None
    raw_line = test.raw_list_line if test is not None else ""
    logical_key = build_logical_key(spec_file, title)
    variant_key = build_variant_key(spec_file, title, project_name)
    selector = build_test_list_selector(
        spec_file=spec_file,
        title=title,
        project_name=project_name,
        raw_list_line=raw_line,
    )
    mcp_rec, mcp_reason = _mcp_recommendation(
        config,
        enriched.suspected_family,
    )
    return RepairContext(
        packet=enriched,
        previous_failures=previous_failures,
        previous_plans=previous_plans,
        logical_key=logical_key,
        variant_key=variant_key,
        project_name=project_name,
        test_list_selector=selector,
        mcp_recommended=mcp_rec,
        mcp_reason=mcp_reason,
    )


def _context_size(context: RepairContext) -> int:
    from attrs import asdict

    return len(json.dumps(asdict(context), default=str))


def trim_repair_context(
    context: RepairContext,
    max_chars: int = MAX_CONTEXT_CHARS,
) -> RepairContext:
    """Return a prompt-sized repair context with omitted-data notes.

    Trimming is deterministic and layered: drop raw payloads first, then large
    previous-failure evidence, then shorten prior plans to summaries. Everything
    dropped is recorded in ``omitted`` so the prompt can note it.
    """

    if _context_size(context) <= max_chars:
        return context

    omitted: list[str] = list(context.omitted)

    # 1. Drop raw JSON payloads (attachment paths remain on the packet).
    packet = evolve(context.packet, payload={})
    previous_failures = tuple(
        evolve(p, payload={}) for p in context.previous_failures
    )
    context = evolve(
        context, packet=packet, previous_failures=previous_failures
    )
    omitted.append("raw JSON payloads omitted to fit context budget")
    if _context_size(context) <= max_chars:
        return evolve(context, omitted=tuple(omitted))

    # 2. Drop previous failure packets beyond the most recent one.
    if len(context.previous_failures) > 1:
        context = evolve(
            context, previous_failures=context.previous_failures[:1]
        )
        omitted.append("older previous-failure packets omitted")
        if _context_size(context) <= max_chars:
            return evolve(context, omitted=tuple(omitted))

    # 3. Summarize prior plans to their first line.
    summaries = tuple(
        (plan.strip().splitlines() or [""])[0][:200]
        for plan in context.previous_plans
    )
    context = evolve(context, previous_plans=summaries)
    omitted.append("previous plans reduced to one-line summaries")
    return evolve(context, omitted=tuple(omitted))
