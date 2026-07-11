"""Structured agent request and output schemas."""

from __future__ import annotations

import json
from pathlib import Path

from attrs import define, field

from ..mcp.models import AgentMcpAttachment


@define
class PlanRequest:
    """Input sent to the planning agent."""

    prompt: str = field()
    work_dir: Path = field()
    timeout_seconds: int = field(default=1800)
    log_dir: Path | None = field(default=None)
    profile: str | None = field(default=None)
    require_schema: bool = field(default=True)
    mcp: AgentMcpAttachment | None = field(default=None)


@define
class ImplementRequest:
    """Input sent to the implementation agent."""

    prompt: str = field()
    work_dir: Path = field()
    timeout_seconds: int = field(default=1800)
    log_dir: Path | None = field(default=None)
    profile: str | None = field(default=None)
    isolated_workspace: bool = field(default=False)
    mcp: AgentMcpAttachment | None = field(default=None)


@define
class InstrumentRequest:
    """Input sent to the instrumentation agent."""

    prompt: str = field()
    work_dir: Path = field()
    timeout_seconds: int = field(default=1800)
    log_dir: Path | None = field(default=None)
    profile: str | None = field(default=None)
    require_schema: bool = field(default=True)
    mcp: AgentMcpAttachment | None = field(default=None)


def plan_output_schema() -> dict[str, object]:
    """Return JSON Schema for planner output."""

    return {
        "type": "object",
        "required": [
            "summary",
            "root_cause_hypothesis",
            "files_to_inspect",
            "files_to_change",
            "steps",
            "risks",
            "verification_commands",
            "external_blocker_signals",
        ],
        "properties": {
            "summary": {"type": "string"},
            "root_cause_hypothesis": {"type": "string"},
            "files_to_inspect": {"type": "array", "items": {"type": "string"}},
            "files_to_change": {"type": "array", "items": {"type": "string"}},
            "steps": {"type": "array", "items": {"type": "string"}},
            "risks": {"type": "array", "items": {"type": "string"}},
            "verification_commands": {
                "type": "array",
                "items": {"type": "string"},
            },
            "external_blocker_signals": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "additionalProperties": False,
    }


def implementation_output_schema() -> dict[str, object]:
    """Return JSON Schema for implementation output."""

    return {
        "type": "object",
        "required": [
            "summary",
            "changed_files",
            "verification_performed",
            "remaining_risk",
        ],
        "properties": {
            "summary": {"type": "string"},
            "changed_files": {"type": "array", "items": {"type": "string"}},
            "verification_performed": {"type": "array", "items": {"type": "string"}},
            "remaining_risk": {"type": "string"},
        },
        "additionalProperties": False,
    }


def schema_json(schema: dict[str, object]) -> str:
    """Serialize a schema dict for CLI ``--output-schema`` flags."""

    return json.dumps(schema, separators=(",", ":"))
