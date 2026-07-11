"""Managed target runtime lifecycle for command runs."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager

from ..config.models import EffectiveConfig
from ..errors import TargetRuntimeError
from .models import RuntimeContext, RuntimeState
from .registry import create_target_runtime


def build_runtime_context(
    config: EffectiveConfig,
    run_id: str,
    *,
    extra_env: Mapping[str, str] | None = None,
) -> RuntimeContext:
    """Build runtime context for one command invocation."""

    env = {**os.environ}
    if extra_env:
        env.update({str(key): str(value) for key, value in extra_env.items()})
    return RuntimeContext(
        project_root=config.project_root,
        state_dir=config.state_dir,
        run_id=run_id,
        config=config,
        env=env,
    )


@contextmanager
def managed_target_runtime(
    config: EffectiveConfig,
    run_id: str,
    *,
    enabled: bool = True,
    outcome_fn: Callable[[], str] | None = None,
) -> Iterator[RuntimeState | None]:
    """Start target support services for one command run."""

    if not enabled or config.target_runtime.backend == "none":
        yield None
        return

    runtime = create_target_runtime(config)
    context = build_runtime_context(config, run_id)
    state = runtime.start(context)
    try:
        runtime.wait_until_ready(context, state)
    except TargetRuntimeError:
        runtime.stop(context, state, "failed")
        raise
    try:
        yield state
    finally:
        outcome = outcome_fn() if outcome_fn is not None else "failed"
        runtime.stop(context, state, outcome)


def runtime_env_for_playwright(state: RuntimeState | None) -> dict[str, str]:
    """Return runtime env values to layer under per-test lease env."""

    if state is None:
        return {}
    return {str(key): str(value) for key, value in state.env.items()}
