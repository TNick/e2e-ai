"""Plan and execute configured target-runtime refresh actions."""

from __future__ import annotations

import fnmatch
import json
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from ..config.models import (
    DockerComposeRuntimeConfig,
    EffectiveConfig,
    RuntimeRefreshConfig,
)
from ..errors import TargetRuntimeError
from .docker_compose import (
    DockerComposeRuntime,
    build_runtime_compose_argv,
    runtime_cwd,
)
from .health import run_health_checks
from .models import RuntimeContext, RuntimeState
from .store import append_runtime_log, log_path, write_command_manifest

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RefreshPlan:
    """Selected refresh actions and their provenance."""

    changed_paths: tuple[str, ...] = ()
    path_actions: tuple[str, ...] = ()
    requested_actions: tuple[str, ...] = ()
    selected_actions: tuple[str, ...] = ()
    ignored_actions: tuple[str, ...] = ()


@dataclass
class RefreshExecution:
    """Outcome of running one refresh plan."""

    plan: RefreshPlan
    ok: bool = True
    error: str | None = None
    commands: list[list[str]] = field(default_factory=list)
    duration_ms: int = 0


def _normalize_path(path: str) -> str:
    return path.replace("\\", "/")


def path_matches_rule(path: str, pattern: str) -> bool:
    """Return whether a project-relative path matches a glob pattern."""

    normalized_path = _normalize_path(path)
    normalized_pattern = _normalize_path(pattern)
    if fnmatch.fnmatchcase(normalized_path, normalized_pattern):
        return True
    return PurePosixPath(normalized_path).match(normalized_pattern)


def actions_for_changed_paths(
    refresh: RuntimeRefreshConfig,
    changed_paths: Sequence[str],
) -> tuple[str, ...]:
    """Return action names matched by configured path rules."""

    selected: list[str] = []
    seen: set[str] = set()
    for rule in refresh.rules:
        if not any(
            path_matches_rule(path, pattern)
            for path in changed_paths
            for pattern in rule.paths
        ):
            continue
        for action_name in rule.actions:
            if action_name in seen:
                continue
            seen.add(action_name)
            selected.append(action_name)
    return tuple(selected)


def plan_runtime_refresh(
    refresh: RuntimeRefreshConfig | None,
    *,
    changed_paths: Sequence[str],
    requested_actions: Sequence[str],
) -> RefreshPlan | None:
    """Build the refresh plan for one implementer round."""

    if refresh is None or not refresh.actions:
        return None

    known = set(refresh.actions)
    path_actions = actions_for_changed_paths(refresh, changed_paths)
    valid_requested = tuple(
        name
        for name in requested_actions
        if name in known and name in refresh.actions
    )
    ignored = tuple(
        sorted({name for name in requested_actions if name not in known})
    )
    if ignored:
        logger.info(
            "ignoring unknown runtime refresh actions: %s",
            ", ".join(ignored),
        )

    selected: list[str] = []
    seen: set[str] = set()
    for action_name in (*path_actions, *valid_requested):
        if action_name in seen:
            continue
        seen.add(action_name)
        selected.append(action_name)

    ordered = tuple(name for name in refresh.action_order if name in seen)
    if not ordered:
        return None

    return RefreshPlan(
        changed_paths=tuple(changed_paths),
        path_actions=path_actions,
        requested_actions=valid_requested,
        selected_actions=ordered,
        ignored_actions=ignored,
    )


def _compose_commands(
    refresh: RuntimeRefreshConfig,
    compose_cfg: DockerComposeRuntimeConfig,
    config: EffectiveConfig,
    action_names: Sequence[str],
) -> list[list[str]]:
    commands: list[list[str]] = []
    for action_name in action_names:
        action = refresh.actions.get(action_name)
        if action is None:
            continue
        for extra in action.compose:
            commands.append(
                build_runtime_compose_argv(
                    compose_cfg,
                    config.project_root,
                    config.project_id,
                    *extra,
                )
            )
    return commands


def write_refresh_report(work_dir: Path, execution: RefreshExecution) -> None:
    """Persist one refresh execution report."""

    payload = {
        "ok": execution.ok,
        "error": execution.error,
        "duration_ms": execution.duration_ms,
        "changed_paths": list(execution.plan.changed_paths),
        "path_actions": list(execution.plan.path_actions),
        "requested_actions": list(execution.plan.requested_actions),
        "ignored_actions": list(execution.plan.ignored_actions),
        "selected_actions": list(execution.plan.selected_actions),
        "commands": execution.commands,
    }
    (work_dir / "refresh-report.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def execute_runtime_refresh(
    runtime: DockerComposeRuntime,
    *,
    context: RuntimeContext,
    state: RuntimeState,
    plan: RefreshPlan,
) -> RefreshExecution:
    """Run configured compose commands for the selected refresh actions."""

    compose_cfg = runtime.compose
    refresh = compose_cfg.refresh
    if refresh is None:
        return RefreshExecution(plan=plan, ok=True)

    commands = _compose_commands(
        refresh,
        compose_cfg,
        runtime.config,
        plan.selected_actions,
    )
    execution = RefreshExecution(plan=plan, commands=commands)
    if not commands:
        return execution

    cwd = runtime_cwd(context.project_root, compose_cfg)
    env = dict(state.env)
    refresh_log = log_path(state.work_dir, "refresh.log")
    started = time.monotonic()

    from ..isolation.docker_compose import run_compose

    for index, argv in enumerate(commands):
        label = f"refresh-{index}"
        write_command_manifest(state.work_dir, argv, label=label)
        code = run_compose(argv, cwd=cwd, env=env, log_path=refresh_log)
        if code != 0:
            execution.ok = False
            execution.error = (
                f"runtime refresh command failed (exit {code}); "
                f"see {refresh_log}"
            )
            execution.duration_ms = int((time.monotonic() - started) * 1000)
            write_refresh_report(state.work_dir, execution)
            return execution

    if compose_cfg.health_checks:
        health_log = log_path(state.work_dir, "health.log")
        try:
            run_health_checks(
                compose_cfg.health_checks,
                cwd=cwd,
                env=env,
                log_path=health_log,
            )
        except TargetRuntimeError as exc:
            append_runtime_log(state.work_dir, "health.log", str(exc))
            execution.ok = False
            execution.error = str(exc)

    execution.duration_ms = int((time.monotonic() - started) * 1000)
    write_refresh_report(state.work_dir, execution)
    return execution
