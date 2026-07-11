"""Playwright test inventory models."""

from __future__ import annotations

from attrs import define, field


@define
class DiscoveredTest:
    """One test discovered from Playwright list output."""

    id: str = field()
    title: str = field()
    spec_file: str = field()
    project_name: str | None = field(default=None)
    line: int | None = field(default=None)
    raw_list_line: str = field(default="")


@define
class TestInventory:
    """Collection of discovered tests."""

    tests: tuple[DiscoveredTest, ...] = field()
    warnings: tuple[str, ...] = field(factory=tuple)


@define
class DiscoveryCounts:
    """Summary counts after inventory refresh."""

    discovered: int = field()
    runnable: int = field()
    excluded: int = field()
    stale: int = field()
