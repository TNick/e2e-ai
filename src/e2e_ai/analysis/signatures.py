"""Failure signatures, packet ids, and generic family classification."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from .text import normalize_error_text

if TYPE_CHECKING:
    from .failure_packet import FailurePacket

# Generic, project-independent failure families.
FAMILY_AUTH = "auth"
FAMILY_NETWORK = "network"
FAMILY_BACKEND_5XX = "backend_5xx"
FAMILY_FRONTEND_EXCEPTION = "frontend_exception"
FAMILY_LOCATOR_TIMEOUT = "locator_timeout"
FAMILY_ASSERTION = "assertion"
FAMILY_TEST_SETUP = "test_setup"
FAMILY_UNKNOWN = "unknown"

GENERIC_FAMILIES = frozenset(
    {
        FAMILY_AUTH,
        FAMILY_NETWORK,
        FAMILY_BACKEND_5XX,
        FAMILY_FRONTEND_EXCEPTION,
        FAMILY_LOCATOR_TIMEOUT,
        FAMILY_ASSERTION,
        FAMILY_TEST_SETUP,
        FAMILY_UNKNOWN,
    }
)

# Families the plan allows project adapters to refine.
REFINABLE_FAMILIES = frozenset(
    {FAMILY_UNKNOWN, FAMILY_ASSERTION, FAMILY_LOCATOR_TIMEOUT}
)


def _first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def build_failure_signature(packet: FailurePacket) -> str:
    """Return the grouping signature for one failure.

    The signature is stable across runs for the "same" failure: it hashes the
    spec file, the family, and the normalized headline of the error, so volatile
    values (timestamps, ports, ids) do not fracture grouping.
    """

    headline = _first_line(normalize_error_text(packet.error_message))
    material = "\n".join([packet.spec_file, packet.suspected_family, headline])
    return hashlib.blake2b(material.encode("utf-8"), digest_size=8).hexdigest()


def build_failure_packet_id(signature: str, attempt_id: str) -> str:
    """Return a stable packet id from the signature and attempt id."""

    digest = hashlib.blake2b(
        f"{signature}:{attempt_id}".encode(), digest_size=8
    ).hexdigest()
    return f"fp_{digest}"


def detect_generic_family(
    spec_file: str, error_message: str, stack: str
) -> str:
    """Classify a failure into a generic, project-independent family."""

    haystack = f"{error_message}\n{stack}".lower()

    def has(*needles: str) -> bool:
        return any(n in haystack for n in needles)

    # Infrastructure signals first (clearest and most actionable).
    if has(
        "econnrefused",
        "connection refused",
        "getaddrinfo",
        "enotfound",
        "socket hang up",
        "network error",
        "err_connection",
    ):
        return FAMILY_NETWORK
    if has(
        " 500",
        "http 500",
        "status 500",
        "502",
        "503",
        "504",
        "internal server error",
        "bad gateway",
        "service unavailable",
    ):
        return FAMILY_BACKEND_5XX
    if has(
        "401",
        "403",
        "unauthorized",
        "forbidden",
        "not authenticated",
        "invalid credentials",
        "login failed",
        "auth",
    ):
        return FAMILY_AUTH
    if has(
        "timeout",
        "timed out",
        "waiting for",
        "exceeded",
    ) and has("locator", "getby", "element", "selector", "tobevisible"):
        return FAMILY_LOCATOR_TIMEOUT
    if has(
        "page.evaluate",
        "uncaught",
        "referenceerror",
        "typeerror",
        "is not a function",
        "cannot read properties",
    ):
        return FAMILY_FRONTEND_EXCEPTION
    if has("beforeall", "beforeeach", "fixture", "setup", "global setup"):
        return FAMILY_TEST_SETUP
    if has(
        "expect(",
        "expected",
        "tobe",
        "toequal",
        "tohavetext",
        "assertion",
        "received",
    ):
        return FAMILY_ASSERTION
    return FAMILY_UNKNOWN
