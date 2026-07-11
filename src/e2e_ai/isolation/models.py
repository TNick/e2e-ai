"""Isolation backend context and lease models."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from attrs import define, field

from ..config.models import EffectiveConfig


@define
class IsolationContext:
    """Context supplied to an isolation backend."""

    project_root: Path = field()
    state_dir: Path = field()
    config: EffectiveConfig = field()
    env: Mapping[str, str] = field()


@define
class EnvironmentLease:
    """Environment allocated for one test attempt."""

    id: str = field()
    test_id: str = field()
    work_dir: Path = field()
    env: Mapping[str, str] = field(factory=dict)
    database_name: str | None = field(default=None)
    frontend_url: str | None = field(default=None)
    backend_url: str | None = field(default=None)
    cleanup_hint: str | None = field(default=None)
