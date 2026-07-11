"""Health checks for target support services."""

from __future__ import annotations

import logging
import socket
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path

from ..config.models import RuntimeHealthCheckConfig
from ..errors import TargetRuntimeError

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 1.0


def _expand_env(value: str, env: Mapping[str, str]) -> str:
    result = value
    for key, item in env.items():
        result = result.replace(f"${{{key}}}", item)
    return result


def wait_for_http_health(
    check: RuntimeHealthCheckConfig,
    env: Mapping[str, str],
    *,
    log_path: Path | None = None,
) -> None:
    """Wait for an HTTP endpoint to return a successful response."""

    url = _expand_env(str(check.url), env)
    deadline = time.monotonic() + check.timeout_seconds
    last_error = "no response"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if 200 <= response.status < 400:
                    return
                last_error = f"HTTP {response.status}"
        except urllib.error.HTTPError as exc:
            if 200 <= exc.code < 400:
                return
            last_error = f"HTTP {exc.code}"
        except urllib.error.URLError as exc:
            last_error = str(exc.reason)
        time.sleep(_POLL_INTERVAL_SECONDS)
    message = (
        f"runtime health check {check.name!r} (http) timed out after "
        f"{check.timeout_seconds}s: {last_error}"
    )
    if log_path is not None:
        message = f"{message}; see {log_path}"
    raise TargetRuntimeError(message)


def wait_for_tcp_health(
    check: RuntimeHealthCheckConfig,
    *,
    log_path: Path | None = None,
) -> None:
    """Wait for a TCP host and port to accept connections."""

    host = str(check.host)
    port = int(check.port)
    deadline = time.monotonic() + check.timeout_seconds
    last_error = "connection refused"
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=3):
                return
        except OSError as exc:
            last_error = str(exc)
        time.sleep(_POLL_INTERVAL_SECONDS)
    message = (
        f"runtime health check {check.name!r} (tcp) timed out after "
        f"{check.timeout_seconds}s: {last_error}"
    )
    if log_path is not None:
        message = f"{message}; see {log_path}"
    raise TargetRuntimeError(message)


def wait_for_command_health(
    check: RuntimeHealthCheckConfig,
    cwd: Path,
    env: Mapping[str, str],
    *,
    log_path: Path | None = None,
) -> None:
    """Wait for a command health check to succeed."""

    deadline = time.monotonic() + check.timeout_seconds
    last_code = 1
    while time.monotonic() < deadline:
        try:
            result = subprocess.run(
                list(check.argv),
                cwd=str(cwd),
                env=dict(env),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except FileNotFoundError as exc:
            raise TargetRuntimeError(
                f"runtime health check {check.name!r} command not found: {exc}"
            ) from exc
        if result.returncode == 0:
            return
        last_code = int(result.returncode)
        time.sleep(_POLL_INTERVAL_SECONDS)
    message = (
        f"runtime health check {check.name!r} (command) timed out after "
        f"{check.timeout_seconds}s (last exit {last_code})"
    )
    if log_path is not None:
        message = f"{message}; see {log_path}"
    raise TargetRuntimeError(message)


def run_health_checks(
    checks: tuple[RuntimeHealthCheckConfig, ...],
    *,
    cwd: Path,
    env: Mapping[str, str],
    log_path: Path,
) -> None:
    """Run configured health checks, logging progress."""

    for check in checks:
        logger.log(1, "waiting for runtime health check %s", check.name)
        if check.kind == "http":
            wait_for_http_health(check, env, log_path=log_path)
        elif check.kind == "tcp":
            wait_for_tcp_health(check, log_path=log_path)
        elif check.kind == "command":
            wait_for_command_health(check, cwd, env, log_path=log_path)
        else:
            raise TargetRuntimeError(
                f"unsupported runtime health check kind {check.kind!r}"
            )
