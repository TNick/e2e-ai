"""Prompt construction for the planner, implementer, and instrumenter agents."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

from .config import EffectiveConfig
from .inventory.models import DiscoveredTest
from .models import FailureInfo
from .repair.store import PlanRecord


def _test_dir(config: EffectiveConfig) -> Path:
    return config.project_root / config.playwright.cwd


def test_selector(test: DiscoveredTest) -> str:
    """Human-readable ``file › title`` label for a discovered test."""

    if test.raw_list_line:
        return test.raw_list_line
    if test.project_name:
        return f"[{test.project_name}] › {test.spec_file} › {test.title}"
    return f"{test.spec_file} › {test.title}"


def _failure_block(failure: FailureInfo) -> str:
    payload = {
        "error_message": failure.error_message,
        "location": failure.location,
        "stack": failure.stack,
        "stdout_tail": failure.stdout_tail,
        "stderr_tail": failure.stderr_tail,
        "attachments": failure.attachments,
        "duration_ms": failure.duration_ms,
    }
    return json.dumps(payload, indent=2)


def _previous_plans_block(plans: list[PlanRecord]) -> str:
    if not plans:
        return "(none — this is the first fix attempt for this test)"
    parts: list[str] = []
    for idx, plan in enumerate(plans, start=1):
        parts.append(
            f"----- PREVIOUS PLAN #{idx} (outcome: {plan.outcome}; "
            f"by: {plan.agent_id or '?'}) -----\n{plan.plan_text.strip()}"
        )
    return "\n\n".join(parts)


def _previous_failures_block(failures: list[dict]) -> str:
    if not failures:
        return "(no earlier recorded failures)"
    lines: list[str] = []
    for idx, fail in enumerate(failures, start=1):
        msg = str(fail.get("error_message", "")).strip().splitlines()[:1]
        phase = fail.get("_phase", "?")
        lines.append(f"{idx}. [{phase}] {(msg[0] if msg else '(no message)')[:200]}")
    return "\n".join(lines)


def build_plan_prompt(
    *,
    test: DiscoveredTest,
    failure: FailureInfo,
    previous_plans: list[PlanRecord],
    previous_failures: list[dict],
    config: EffectiveConfig,
    workdir: Path,
    instrument: bool = False,
) -> str:
    """Prompt for the smart planner (or the 2nd-pass instrumenter).

    ``instrument=True`` switches the framing to the escalation path used when a
    test still fails after a plan was implemented: the agent is told to add
    diagnostic logging/instrumentation and prove the real code path before
    proposing another behavioral change.
    """

    role = "instrumentation" if instrument else "planning"
    escalation = ""
    if instrument:
        escalation = textwrap.dedent(
            """
            ESCALATION: This test already failed again after a previous plan was
            implemented. Do NOT propose another blind behavioral change. First
            add detailed, targeted debug logging/instrumentation around the
            failing code path and describe exactly what to observe on the next
            run to prove the real root cause. Only then propose the minimal fix.
            """
        ).strip()

    return (
        textwrap.dedent(
            f"""
        You are the {role} agent in an automated Playwright e2e fix loop.
        Your job is to produce a concrete, step-by-step PLAN that another,
        cheaper agent will implement verbatim. Do not edit files yourself in
        this step — only produce the plan.

        Project root: {config.project_root}
        Playwright test dir: {_test_dir(config)}
        Per-test working directory (scratch space for notes): {workdir}

        {escalation}

        Test under repair:
        - id: {test.id}
        - project: {test.project_name or "(default)"}
        - spec file: {test.spec_file}
        - title: {test.title}

        Latest failure:
        {_failure_block(failure)}

        Earlier failures for THIS test (most recent first — if it has passed
        before, prefer a fix that respects that history):
        {_previous_failures_block(previous_failures)}

        Previous plans already tried for this test (they did NOT fix it — do not
        repeat them; explain why the next plan is different):
        {_previous_plans_block(previous_plans)}

        Constraints:
        - Fix the product or test code so the test passes for the right reason.
        - Do NOT skip the test, add arbitrary sleeps, weaken assertions, or hide
          the failure.
        - Prefer the smallest change that addresses the root cause.
        - If the failure is clearly caused by test setup or the environment
          (services down, missing browsers, DB not reachable, auth/tokens) and
          NOT by the code under test, say so explicitly and start your plan with
          the single line: `BLOCKED: <reason>`.

        Output format (markdown):
        1. Root-cause hypothesis.
        2. Numbered implementation steps (files + what to change).
        3. How to verify (the exact rerun command is handled by the loop).
        """
        ).strip()
        + "\n"
    )


def build_implement_prompt(
    *,
    test: DiscoveredTest,
    plan_text: str,
    config: EffectiveConfig,
    workdir: Path,
) -> str:
    """Prompt for the cheap implementer agent."""

    return (
        textwrap.dedent(
            f"""
        You are the implementer agent in an automated Playwright e2e fix loop.
        Apply the following PLAN by editing files in the project. Make the
        changes exactly as described; do not redesign the approach. If a step is
        impossible, implement what you can and note the discrepancy at the end.

        Project root: {config.project_root}
        Playwright test dir: {_test_dir(config)}
        Working directory for scratch notes: {workdir}

        Test being fixed:
        - {test.spec_file} › {test.title} (project: {test.project_name or "default"})

        Rules:
        - Do NOT skip the test, add sleeps, or weaken assertions.
        - Keep the change minimal and focused on the plan.
        - Do not revert unrelated changes in the working tree.

        ----- BEGIN PLAN -----
        {plan_text.strip()}
        ----- END PLAN -----

        When done, briefly list the files you changed.
        """
        ).strip()
        + "\n"
    )


def plan_is_blocked(plan_text: str) -> str | None:
    """Return the blockage reason if the planner declared the test BLOCKED."""

    for line in plan_text.strip().splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        upper = stripped.upper()
        if upper.startswith("BLOCKED:"):
            return stripped[len("BLOCKED:") :].strip() or "blocked by planner"
        if upper.startswith("BLOCKED_REFERENCE_BACKEND"):
            suffix = stripped.split(":", 1)
            if len(suffix) > 1:
                return suffix[1].strip() or "reference backend fix required"
            return "reference backend fix required"
        return None
    return None
