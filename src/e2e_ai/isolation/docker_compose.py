"""Docker Compose argv builders and subprocess helpers."""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

from ..errors import DockerError
from .models import IsolationContext

logger = logging.getLogger(__name__)


def build_compose_argv(
    compose_files: Sequence[Path],
    project_name: str,
    env_file: Path | None,
    *extra: str,
) -> list[str]:
    """Build a docker compose argv list."""

    argv = ["docker", "compose", "-p", project_name]
    if env_file is not None:
        argv.extend(["--env-file", str(env_file)])
    for compose_file in compose_files:
        argv.extend(["-f", str(compose_file)])
    argv.extend(extra)
    return argv


def run_compose(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    log_path: Path | None = None,
) -> int:
    """Run docker compose and optionally log output."""

    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8", errors="replace") as handle:
            handle.write("\n$ {}\n".format(" ".join(argv)))
            handle.flush()
            try:
                process = subprocess.Popen(
                    list(argv),
                    cwd=str(cwd),
                    env=dict(env),
                    stdout=handle,
                    stderr=subprocess.STDOUT,
                )
            except FileNotFoundError as exc:
                raise DockerError("docker not found on PATH") from exc
            return process.wait()
    try:
        result = subprocess.run(
            list(argv),
            cwd=str(cwd),
            env=dict(env),
            check=False,
        )
    except FileNotFoundError as exc:
        raise DockerError("docker not found on PATH") from exc
    return int(result.returncode)


def _compose_files(context: IsolationContext) -> list[Path]:
    pg = context.config.isolation.postgres
    compose = (context.project_root / pg.compose_file).resolve()
    return [compose]


def _compose_project_name(context: IsolationContext) -> str:
    pg = context.config.isolation.postgres
    if pg.compose_project_name:
        return pg.compose_project_name
    safe = (
        "".join(
            ch if ch.isalnum() else "_" for ch in context.config.project_id.lower()
        ).strip("_")
        or "project"
    )
    return f"e2e_ai_{safe}"


def _env_file(context: IsolationContext) -> Path | None:
    pg = context.config.isolation.postgres
    if not pg.env_file:
        return None
    return (context.project_root / pg.env_file).resolve()


def start_long_lived_services(context: IsolationContext) -> None:
    """Start baseline long-lived services such as PostgreSQL."""

    pg = context.config.isolation.postgres
    services = pg.long_lived_services or (pg.service,)
    argv = build_compose_argv(
        _compose_files(context),
        _compose_project_name(context),
        _env_file(context),
        "up",
        "-d",
        "--wait",
        "--wait-timeout",
        "1800",
        *services,
    )
    code = run_compose(
        argv,
        cwd=context.project_root,
        env=context.env,
        log_path=context.state_dir / "isolation" / "compose-up.log",
    )
    if code != 0:
        raise DockerError(
            f"docker compose up failed for long-lived services (exit {code})"
        )


def run_one_shot_services(context: IsolationContext) -> None:
    """Run one-shot setup services separately from health waits."""

    pg = context.config.isolation.postgres
    log_dir = context.state_dir / "isolation"
    for service in pg.one_shot_services:
        argv = build_compose_argv(
            _compose_files(context),
            _compose_project_name(context),
            _env_file(context),
            "run",
            "--rm",
            service,
        )
        code = run_compose(
            argv,
            cwd=context.project_root,
            env=context.env,
            log_path=log_dir / f"compose-run-{service}.log",
        )
        if code != 0:
            raise DockerError(f"docker compose run --rm {service} failed (exit {code})")
