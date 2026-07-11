"""Models for target support-service lifecycle."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from attrs import define, field

from ..config.models import EffectiveConfig


@define
class RuntimeContext:
    """Context supplied to target runtime backends."""

    project_root: Path = field()
    state_dir: Path = field()
    run_id: str = field()
    config: EffectiveConfig = field()
    env: Mapping[str, str] = field()


@define
class RuntimeState:
    """State returned after target runtime startup."""

    id: str = field()
    backend: str = field()
    work_dir: Path = field()
    env: Mapping[str, str] = field(factory=dict)
    started: bool = field(default=False)
    healthy: bool = field(default=False)
    cleanup_hint: str | None = field(default=None)
