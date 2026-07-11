"""Normalized failure packet built from one failing Playwright attempt."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol

from attrs import define, field

from ..inventory.models import DiscoveredTest
from ..isolation.models import EnvironmentLease
from ..models import FailureInfo
from ..runner.models import TestRunResult
from ..runner.results import extract_failure
from .attachments import extract_attachment_paths, find_error_context
from .signatures import (
    REFINABLE_FAMILIES,
    build_failure_packet_id,
    build_failure_signature,
    detect_generic_family,
)
from .text import (
    normalize_error_text,
    redact_sensitive_json,
    redact_sensitive_text,
    tail_lines,
)


class FailureClassifier(Protocol):
    """Optional project hook for classifying failures into domain families."""

    def classify_failure(
        self,
        *,
        spec_file: str,
        test_title: str,
        error_message: str,
        stack: str,
        attachments: Sequence[Path],
    ) -> str | None:
        """Return a project-specific family or ``None`` to keep the generic one."""


@define
class FailurePacket:
    """Normalized description of one failing Playwright attempt."""

    id: str = field()
    test_id: str = field()
    attempt_id: str = field()
    signature: str = field()
    spec_file: str = field()
    test_title: str = field()
    error_message: str = field()
    stack: str = field()
    stdout_tail: str = field()
    stderr_tail: str = field()
    screenshot_paths: tuple[str, ...] = field(factory=tuple)
    trace_paths: tuple[str, ...] = field(factory=tuple)
    error_context_path: str | None = field(default=None)
    frontend_url: str | None = field(default=None)
    backend_url: str | None = field(default=None)
    database_name: str | None = field(default=None)
    suspected_family: str = field(default="unknown")
    payload: Mapping[str, object] = field(factory=dict)
    # Flake evidence (populated by the context layer against run history).
    is_repeat_signature: bool = field(default=False)
    previous_pass_count: int = field(default=0)
    previous_fail_count: int = field(default=0)
    last_passed_at: str | None = field(default=None)
    flake_evidence: str | None = field(default=None)


def _read_log(attempt: TestRunResult) -> str:
    path = attempt.stdout_path
    if path is not None and Path(path).is_file():
        return Path(path).read_text(encoding="utf-8", errors="replace")
    return ""


def build_failure_packet(
    *,
    test: DiscoveredTest,
    attempt: TestRunResult,
    report: Mapping[str, object] | None,
    lease: EnvironmentLease | None = None,
    classifier: FailureClassifier | None = None,
    failure: FailureInfo | None = None,
) -> FailurePacket:
    """Build a failure packet from one failed attempt.

    Text is normalized and secret-redacted before it is stored. Binary
    artifacts are referenced by path, never embedded. When there is no JSON
    report (timeout / runner error), the runner's already-extracted ``failure``
    is used so the packet keeps the best available error message.
    """

    log_text = _read_log(attempt)
    if report:
        info = extract_failure(report, log_text)
    elif failure is not None:
        info = failure
    else:
        info = extract_failure(None, log_text)

    error_message = normalize_error_text(redact_sensitive_text(info.error_message))
    stack = normalize_error_text(redact_sensitive_text(info.stack))
    stdout_tail = "\n".join(tail_lines(redact_sensitive_text(log_text)))

    work_dir = attempt.work_dir or (Path(attempt.stdout_path).parent)
    screenshots, traces = extract_attachment_paths(report or {}, Path(work_dir))
    error_context = find_error_context(Path(work_dir), test)

    family = detect_generic_family(test.spec_file, error_message, stack)
    if classifier is not None and family in REFINABLE_FAMILIES:
        refined = classifier.classify_failure(
            spec_file=test.spec_file,
            test_title=test.title,
            error_message=error_message,
            stack=stack,
            attachments=[*screenshots, *traces],
        )
        if refined:
            family = refined

    env = lease.env if lease is not None else {}
    packet = FailurePacket(
        id="",
        test_id=test.id,
        attempt_id=attempt.attempt_id,
        signature="",
        spec_file=test.spec_file,
        test_title=test.title,
        error_message=error_message or "test failed (no structured error)",
        stack=stack,
        stdout_tail=stdout_tail,
        stderr_tail="",  # stdout and stderr share one combined log
        screenshot_paths=tuple(str(p) for p in screenshots),
        trace_paths=tuple(str(p) for p in traces),
        error_context_path=str(error_context) if error_context else None,
        frontend_url=(lease.frontend_url if lease else None),
        backend_url=(lease.backend_url if lease else None),
        database_name=(lease.database_name if lease else attempt.database_name),
        suspected_family=family,
        payload=redact_sensitive_json(dict(report)) if report else {},
    )
    packet.signature = build_failure_signature(packet)
    packet.id = build_failure_packet_id(packet.signature, attempt.attempt_id)
    # Note the isolation env in the payload for reproduction (already redacted).
    if env:
        payload = dict(packet.payload)
        payload["environment_keys"] = sorted(env.keys())
        packet.payload = payload
    return packet
