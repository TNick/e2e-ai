"""Shared health and login probe helpers."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

from .capabilities import (
    QUOTA_AUTH_ERROR,
    QUOTA_MISCONFIGURED,
    QUOTA_READY,
    QUOTA_UNKNOWN,
    AgentHealth,
)


def expand_path(path: str) -> Path:
    """Expand user and environment variables in a path."""

    return Path(os.path.expandvars(os.path.expanduser(path)))


def auth_file_present(paths: Sequence[str]) -> Path | None:
    """Return the first existing non-empty credential file."""

    for candidate in paths:
        path = expand_path(candidate)
        if path.is_file() and path.stat().st_size > 0:
            return path
    return None


def run_probe(
    executable: str,
    argv: Sequence[str],
    *,
    env: dict[str, str] | None = None,
    timeout_seconds: int = 30,
) -> tuple[bool, str]:
    """Run a short probe command and return success plus output tail."""

    exe = shutil.which(executable)
    if exe is None:
        return False, f"{executable!r} not found on PATH"
    run_env = {**os.environ}
    if env:
        run_env.update(env)
    try:
        result = subprocess.run(
            [exe, *argv],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
            env=run_env,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return False, str(exc)
    out = "\n".join(
        part for part in (result.stdout, result.stderr) if part
    ).strip()
    return result.returncode == 0, out[-500:]


def health_from_probe(
    agent_id: str,
    *,
    executable: str,
    auth_files: Sequence[str],
    login_argv: Sequence[str] | None,
    health_argv: Sequence[str] | None = None,
    env: dict[str, str] | None = None,
) -> AgentHealth:
    """Build an :class:`AgentHealth` from credential files and probes."""

    if shutil.which(executable) is None:
        return AgentHealth(
            agent_id=agent_id,
            logged_in=False,
            verified=True,
            reason=f"{executable!r} not found on PATH",
            state=QUOTA_MISCONFIGURED,
        )
    cred = auth_file_present(auth_files)
    if cred is not None:
        return AgentHealth(
            agent_id=agent_id,
            logged_in=True,
            verified=True,
            reason=f"credentials present at {cred}",
            state=QUOTA_READY,
        )
    if login_argv:
        ok, detail = run_probe(executable, login_argv, env=env)
        return AgentHealth(
            agent_id=agent_id,
            logged_in=ok,
            verified=True,
            reason=detail or ("login check ok" if ok else "login check failed"),
            state=QUOTA_READY if ok else QUOTA_AUTH_ERROR,
        )
    probe_argv = health_argv or ("--version",)
    ok, detail = run_probe(executable, probe_argv, env=env)
    if not ok:
        return AgentHealth(
            agent_id=agent_id,
            logged_in=False,
            verified=True,
            reason=detail or "health probe failed",
            state=QUOTA_MISCONFIGURED,
        )
    return AgentHealth(
        agent_id=agent_id,
        logged_in=True,
        verified=False,
        reason="binary responds but login could not be verified without tokens",
        state=QUOTA_UNKNOWN,
    )
