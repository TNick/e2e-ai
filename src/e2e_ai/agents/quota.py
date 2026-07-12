"""Quota observation and reservation helpers."""

from __future__ import annotations

import time
from collections.abc import Mapping

from attrs import define, field

from .capabilities import (
    QUOTA_DEGRADED,
    QUOTA_EXHAUSTED,
    QUOTA_READY,
    QUOTA_UNKNOWN,
)

# Expected quota cost by task class (relative units).
_TASK_CLASS_COST: Mapping[str, int] = {
    "short": 1,
    "normal": 2,
    "difficult": 4,
    "long": 6,
}


@define
class QuotaWindow:
    """One observed quota window."""

    label: str = field()
    used_percent: int | None = field(default=None)
    resets_at: str | None = field(default=None)


@define
class QuotaSnapshot:
    """Normalized quota state for an agent."""

    plugin_id: str = field()
    state: str = field(default=QUOTA_UNKNOWN)
    confidence: str = field(default="low")
    task_classes: Mapping[str, str] = field(factory=dict)
    windows: tuple[QuotaWindow, ...] = field(factory=tuple)
    observed_at: float = field(factory=time.time)
    optimistic: bool = field(default=False)
    detail: str = field(default="")


@define
class QuotaReservation:
    """Reserved quota headroom for one planned invocation."""

    plugin_id: str = field()
    task_class: str = field()
    units: int = field()
    optimistic: bool = field(default=False)


_ACTIVE_RESERVATIONS: dict[str, int] = {}


def enough_quota(task_class: str, snapshot: QuotaSnapshot) -> bool:
    """Return whether a task can start under current policy."""

    if snapshot.state == QUOTA_EXHAUSTED:
        return False
    if snapshot.state in {QUOTA_READY, QUOTA_DEGRADED}:
        return True
    if snapshot.state == QUOTA_UNKNOWN:
        return snapshot.optimistic
    per_class = snapshot.task_classes.get(task_class)
    if per_class == QUOTA_EXHAUSTED:
        return False
    return snapshot.state != QUOTA_EXHAUSTED


def reserve_quota(
    plugin_id: str,
    task_class: str,
    snapshot: QuotaSnapshot,
) -> QuotaReservation:
    """Reserve expected quota headroom for a planned invocation."""

    units = _TASK_CLASS_COST.get(task_class, 2)
    optimistic = snapshot.state == QUOTA_UNKNOWN
    if enough_quota(task_class, snapshot):
        _ACTIVE_RESERVATIONS[plugin_id] = (
            _ACTIVE_RESERVATIONS.get(plugin_id, 0) + units
        )
        return QuotaReservation(
            plugin_id=plugin_id,
            task_class=task_class,
            units=units,
            optimistic=optimistic,
        )
    return QuotaReservation(
        plugin_id=plugin_id,
        task_class=task_class,
        units=0,
        optimistic=False,
    )


def release_quota(reservation: QuotaReservation) -> None:
    """Release or finalize a quota reservation."""

    current = _ACTIVE_RESERVATIONS.get(reservation.plugin_id, 0)
    remaining = max(0, current - reservation.units)
    if remaining:
        _ACTIVE_RESERVATIONS[reservation.plugin_id] = remaining
    else:
        _ACTIVE_RESERVATIONS.pop(reservation.plugin_id, None)


def invalidate_quota_cache() -> None:
    """Clear in-memory reservation counters (used after invocations)."""

    _ACTIVE_RESERVATIONS.clear()
