"""Failure context and instrumentation: turn a failed attempt into evidence.

This package prepares durable, secret-redacted failure packets, previous-context
loading, an instrumentation policy, and serialized patch application. Agent
prompt construction is a separate, later step — this layer only prepares
structured evidence. See `research/16. Failure Context and Instrumentation.md`.
"""

from __future__ import annotations

from .attachments import extract_attachment_paths, find_error_context
from .context import (
    RepairContext,
    build_logical_key,
    build_repair_context,
    build_test_list_selector,
    build_variant_key,
    load_previous_failures,
    load_previous_plans,
    trim_repair_context,
)
from .failure_packet import FailureClassifier, FailurePacket, build_failure_packet
from .instrumentation import (
    TEMP_MARKER,
    build_instrumentation_request,
    should_instrument,
)
from .patches import (
    PatchApplyResult,
    PatchArtifact,
    apply_patch_atomically,
    create_patch_from_worktree,
    validate_patch_applies,
)
from .signatures import (
    build_failure_packet_id,
    build_failure_signature,
    detect_generic_family,
)
from .store import get_failure_packet, insert_failure_packet
from .text import (
    normalize_error_text,
    redact_sensitive_json,
    redact_sensitive_text,
    strip_ansi,
    tail_lines,
)

__all__ = [
    "FailureClassifier",
    "FailurePacket",
    "PatchApplyResult",
    "PatchArtifact",
    "RepairContext",
    "TEMP_MARKER",
    "apply_patch_atomically",
    "build_failure_packet",
    "build_failure_packet_id",
    "build_failure_signature",
    "build_instrumentation_request",
    "build_logical_key",
    "build_repair_context",
    "build_test_list_selector",
    "build_variant_key",
    "create_patch_from_worktree",
    "detect_generic_family",
    "extract_attachment_paths",
    "find_error_context",
    "get_failure_packet",
    "insert_failure_packet",
    "load_previous_failures",
    "load_previous_plans",
    "normalize_error_text",
    "redact_sensitive_json",
    "redact_sensitive_text",
    "should_instrument",
    "strip_ansi",
    "tail_lines",
    "trim_repair_context",
    "validate_patch_applies",
]
