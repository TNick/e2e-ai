"""Docker Compose target runtime backend."""

from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Mapping
from pathlib import Path

from attrs import define, field

from ..config.models import DockerComposeRuntimeConfig, EffectiveConfig
from ..errors import TargetRuntimeError
from ..isolation.docker_compose import run_compose
from .health import run_health_checks
from .models import RuntimeContext, RuntimeState
from .store import (
    append_runtime_log,
    log_path,
    runtime_work_dir,
    write_command_manifest,
    write_compose_ps_output,
    write_runtime_state,
)

logger = logging.getLogger(__name__)


def _default_project_name(project_id: str) -> str:
    safe = "".join(
        ch if ch.isalnum() else "_" for ch in project_id.lower()
    ).strip("_")
    return f"e2e_ai_{safe or 'project'}"


def resolve_runtime_path(
    project_root: Path,
    raw: str,
    *,
    base: Path | None = None,
) -> Path:
    """Resolve a runtime path relative to the project root or runtime cwd."""

    path = Path(raw)
    if path.is_absolute():
        return path.resolve()
    root = base or project_root
    return (root / path).resolve()


def runtime_cwd(project_root: Path, cfg: DockerComposeRuntimeConfig) -> Path:
    """Return the working directory for runtime compose commands."""

    return resolve_runtime_path(project_root, cfg.cwd)


def build_runtime_compose_argv(
    cfg: DockerComposeRuntimeConfig,
    project_root: Path,
    project_id: str,
    *extra: str,
) -> list[str]:
    """Build a docker compose argv list for target runtime commands."""

    cwd = runtime_cwd(project_root, cfg)
    project_name = cfg.project_name or _default_project_name(project_id)
    argv: list[str] = ["docker", "compose", "-p", project_name]
    for env_file in cfg.env_files:
        argv.extend(
            [
                "--env-file",
                str(resolve_runtime_path(project_root, env_file, base=cwd)),
            ]
        )
    for compose_file in cfg.compose_files:
        argv.extend(
            [
                "-f",
                str(resolve_runtime_path(project_root, compose_file, base=cwd)),
            ]
        )
    for profile in cfg.profiles:
        argv.extend(["--profile", profile])
    argv.extend(extra)
    return argv


def _runtime_env(
    context: RuntimeContext,
    cfg: DockerComposeRuntimeConfig,
) -> dict[str, str]:
    env = {**context.env, **dict(cfg.env)}
    for _profile in cfg.profiles:
        env.setdefault("COMPOSE_PROFILES", ",".join(cfg.profiles))
        break
    return env


def _health_checks_pass(
    cfg: DockerComposeRuntimeConfig,
    *,
    cwd: Path,
    env: Mapping[str, str],
) -> bool:
    if not cfg.health_checks:
        return False
    try:
        run_health_checks(
            cfg.health_checks,
            cwd=cwd,
            env=env,
            log_path=Path(os.devnull),
        )
    except TargetRuntimeError:
        return False
    return True


@define
class DockerComposeRuntime:
    """Target runtime backed by Docker Compose."""

    config: EffectiveConfig = field()
    compose: DockerComposeRuntimeConfig = field()

    def start(self, context: RuntimeContext) -> RuntimeState:
        cfg = self.compose
        work_dir = runtime_work_dir(context.state_dir, context.run_id)
        cwd = runtime_cwd(context.project_root, cfg)
        env = _runtime_env(context, cfg)
        state = RuntimeState(
            id=f"runtime-{context.run_id}",
            backend="docker_compose",
            work_dir=work_dir,
            env=env,
            cleanup_hint="docker compose stack",
        )

        if _health_checks_pass(cfg, cwd=cwd, env=env):
            logger.log(1, "target runtime already healthy; skipping compose up")
            state.started = False
            state.healthy = True
            write_runtime_state(work_dir, state)
            return state

        argv = build_runtime_compose_argv(
            cfg,
            context.project_root,
            context.config.project_id,
            cfg.start.command,
        )
        if cfg.start.detach:
            argv.append("-d")
        if cfg.start.build:
            argv.append("--build")
        if cfg.start.remove_orphans:
            argv.append("--remove-orphans")
        if cfg.start.wait:
            argv.extend(
                ["--wait", "--wait-timeout", str(cfg.start.timeout_seconds)]
            )
        argv.extend(cfg.services)

        write_command_manifest(work_dir, argv, label="compose-up")
        startup_log = log_path(work_dir, "startup.log")
        code = run_compose(argv, cwd=cwd, env=env, log_path=startup_log)
        if code != 0:
            raise TargetRuntimeError(
                "docker compose up failed for target runtime "
                f"(exit {code}); see {startup_log}. "
                "Inspect the stack with: docker compose ps"
            )
        state.started = True
        write_runtime_state(work_dir, state)
        return state

    def wait_until_ready(
        self,
        context: RuntimeContext,
        state: RuntimeState,
    ) -> None:
        cfg = self.compose
        if not cfg.health_checks:
            state.healthy = True
            write_runtime_state(state.work_dir, state)
            return
        cwd = runtime_cwd(context.project_root, cfg)
        env = dict(state.env)
        health_log = log_path(state.work_dir, "health.log")
        try:
            run_health_checks(
                cfg.health_checks,
                cwd=cwd,
                env=env,
                log_path=health_log,
            )
        except TargetRuntimeError as exc:
            append_runtime_log(state.work_dir, "health.log", str(exc))
            raise
        state.healthy = True
        write_runtime_state(state.work_dir, state)
        self._capture_ps(context, state)

    def stop(
        self,
        context: RuntimeContext,
        state: RuntimeState,
        outcome: str,
    ) -> None:
        cfg = self.compose
        policy = cfg.stop.policy
        if policy == "never":
            return
        if policy == "on_success" and outcome not in ("passed", "success"):
            return
        argv = build_runtime_compose_argv(
            cfg,
            context.project_root,
            context.config.project_id,
            cfg.stop.command,
        )
        if cfg.stop.remove_volumes:
            argv.append("--volumes")
        if cfg.start.remove_orphans:
            argv.append("--remove-orphans")
        write_command_manifest(state.work_dir, argv, label="compose-down")
        cwd = runtime_cwd(context.project_root, cfg)
        env = dict(state.env)
        startup_log = log_path(state.work_dir, "startup.log")
        code = run_compose(argv, cwd=cwd, env=env, log_path=startup_log)
        if code != 0:
            raise TargetRuntimeError(
                "docker compose down failed for target runtime "
                f"(exit {code}); see {startup_log}"
            )

    def _capture_ps(self, context: RuntimeContext, state: RuntimeState) -> None:
        argv = build_runtime_compose_argv(
            self.compose,
            context.project_root,
            context.config.project_id,
            "ps",
            "--format",
            "json",
        )
        cwd = runtime_cwd(context.project_root, self.compose)
        try:
            result = subprocess.run(
                argv,
                cwd=str(cwd),
                env=dict(state.env),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except FileNotFoundError:
            logger.log(1, "docker not found while capturing compose ps output")
            return
        if result.stdout.strip():
            write_compose_ps_output(state.work_dir, result.stdout)


def create_docker_compose_runtime(
    config: EffectiveConfig,
) -> DockerComposeRuntime:
    """Create a Docker Compose runtime from effective config."""

    compose = config.target_runtime.docker_compose
    if compose is None:
        raise TargetRuntimeError(
            "target_runtime.backend docker_compose requires compose settings"
        )
    return DockerComposeRuntime(config=config, compose=compose)
