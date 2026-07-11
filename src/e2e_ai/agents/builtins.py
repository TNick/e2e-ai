"""Built-in agent specifications for Claude Code, Codex, and Cursor.

These are sensible, unattended-friendly defaults. Every field can be overridden
from config under the agent's entry (``agents.<id>``) — the invocation flags of
these CLIs change over time, so treat the values here as a starting point, not
gospel.

Notes on the default invocations (all run headless and are allowed to edit the
working tree without interactive approval, which is what an unattended fix loop
requires):

* **claude** — Claude Code CLI. ``-p`` runs headless (print mode);
  ``--dangerously-skip-permissions`` lets it use tools without prompting. The
  prompt is piped on stdin. Login is proven by the credentials file written by
  ``claude`` / ``claude login`` (no tokens spent).
* **codex** — OpenAI Codex CLI. ``exec`` is the non-interactive mode;
  ``--dangerously-bypass-approvals-and-sandbox`` lets it run commands and edit
  files. The prompt is passed as the trailing argument.
* **cursor** — Cursor's ``cursor-agent`` CLI. ``-p`` is print/non-interactive
  mode and ``--force`` auto-approves edits. The prompt is the trailing argument.

Profiles (``difficult`` / ``cheap``) select a cost/capability tier within one
CLI so a planner role can use a stronger model than the implementer role.
"""

from __future__ import annotations

from .base import AgentSpec

CLAUDE = AgentSpec(
    id="claude",
    executable="claude",
    prompt_args=["--dangerously-skip-permissions", "-p"],
    transport="stdin",
    health_args=["--version"],
    auth_files=[
        "~/.claude/.credentials.json",
        "~/.config/claude/.credentials.json",
    ],
    profiles={
        "difficult": ["--model", "opus"],
        "cheap": ["--model", "haiku"],
    },
)

CODEX = AgentSpec(
    id="codex",
    executable="codex",
    prompt_args=["exec", "--dangerously-bypass-approvals-and-sandbox"],
    transport="argument",
    health_args=["--version"],
    auth_files=["~/.codex/auth.json"],
    profiles={
        "difficult": ["-c", "model_reasoning_effort=high"],
        "cheap": ["-c", "model_reasoning_effort=low"],
    },
)

CURSOR = AgentSpec(
    id="cursor",
    executable="cursor-agent",
    prompt_args=["-p", "--force"],
    transport="argument",
    health_args=["--version"],
    # cursor-agent has no token-free credential file we can rely on across
    # platforms; ``status`` reports auth without running a model turn.
    login_check_args=["status"],
    auth_files=[
        "~/.local/share/cursor-agent/credentials.json",
        "~/.cursor/cli-config.json",
    ],
)

BUILTIN_SPECS: dict[str, AgentSpec] = {
    spec.id: spec for spec in (CLAUDE, CODEX, CURSOR)
}
