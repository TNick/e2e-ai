"""Persist discovered tests in the state database."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ..db import database_path, ensure_database, transaction
from .models import DiscoveredTest, DiscoveryCounts, TestInventory
from .playwright_list import parse_playwright_list, run_playwright_list

if TYPE_CHECKING:
    # Type-only import to avoid a config -> mcp -> analysis -> inventory cycle.
    from ..config.models import EffectiveConfig

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _config_hash(config: EffectiveConfig) -> str:
    payload = {
        "exclude": list(config.exclude),
        "playwright_cwd": config.playwright.cwd,
        "project_root": str(config.project_root),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _selector(test: DiscoveredTest) -> str:
    if test.raw_list_line:
        return test.raw_list_line
    if test.project_name:
        return f"[{test.project_name}] › {test.spec_file} › {test.title}"
    return f"{test.spec_file} › {test.title}"


def _exclude_match_fields(test: DiscoveredTest) -> tuple[str, ...]:
    """Return fields to match exclude patterns against."""

    spec_file = test.spec_file.replace("\\", "/")
    fields = (
        _selector(test),
        spec_file,
        test.title,
        test.id,
    )
    if "/" not in spec_file:
        # Playwright list output often omits the tests/ prefix from spec paths.
        fields = (*fields, f"tests/{spec_file}")
    return fields


def apply_excludes(
    tests: Sequence[DiscoveredTest],
    patterns: Sequence[str],
) -> dict[str, str | None]:
    """Return test id to exclude reason."""

    compiled = [re.compile(pattern) for pattern in patterns]
    reasons: dict[str, str | None] = {}
    for test in tests:
        reason: str | None = None
        candidates = _exclude_match_fields(test)
        for pattern in compiled:
            if any(pattern.search(field) for field in candidates):
                reason = f"matched exclude pattern {pattern.pattern!r}"
                break
        reasons[test.id] = reason
    return reasons


def _row_to_discovered_test(row: sqlite3.Row) -> DiscoveredTest:
    return DiscoveredTest(
        id=str(row["id"]),
        title=str(row["title"]),
        spec_file=str(row["spec_file"]),
        project_name=str(row["project_name"]) if row["project_name"] else None,
        line=int(row["line"]) if row["line"] is not None else None,
        raw_list_line=str(row["raw_list_line"]),
    )


def refresh_inventory(
    conn: sqlite3.Connection,
    config: EffectiveConfig,
    inventory: TestInventory,
) -> None:
    """Upsert discovered tests and mark missing tests stale."""

    now = _utc_now_iso()
    project_id = config.project_id
    root_path = str(config.project_root)
    config_hash = _config_hash(config)

    conn.execute(
        """
        INSERT INTO projects (id, root_path, config_hash, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            root_path = excluded.root_path,
            config_hash = excluded.config_hash,
            updated_at = excluded.updated_at
        """,
        (project_id, root_path, config_hash, now, now),
    )

    exclude_reasons = apply_excludes(inventory.tests, config.exclude)
    seen_ids: set[str] = set()

    for test in inventory.tests:
        reason = exclude_reasons.get(test.id)
        excluded = 1 if reason else 0
        conn.execute(
            """
            INSERT INTO tests (
                id,
                project_id,
                title,
                spec_file,
                project_name,
                line,
                raw_list_line,
                excluded,
                exclude_reason,
                is_stale,
                stale_at,
                first_seen_at,
                last_seen_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title = excluded.title,
                spec_file = excluded.spec_file,
                project_name = excluded.project_name,
                line = excluded.line,
                raw_list_line = excluded.raw_list_line,
                excluded = excluded.excluded,
                exclude_reason = excluded.exclude_reason,
                is_stale = 0,
                stale_at = NULL,
                last_seen_at = excluded.last_seen_at
            """,
            (
                test.id,
                project_id,
                test.title,
                test.spec_file,
                test.project_name,
                test.line,
                test.raw_list_line,
                excluded,
                reason,
                now,
                now,
            ),
        )
        seen_ids.add(test.id)

    if seen_ids:
        placeholders = ", ".join("?" for _ in seen_ids)
        params: list[object] = [now, project_id, *sorted(seen_ids)]
        conn.execute(
            f"""
            UPDATE tests
            SET is_stale = 1, stale_at = ?
            WHERE project_id = ?
              AND id NOT IN ({placeholders})
            """,
            params,
        )
    else:
        conn.execute(
            """
            UPDATE tests
            SET is_stale = 1, stale_at = ?
            WHERE project_id = ?
            """,
            (now, project_id),
        )

    logger.log(
        1,
        "refreshed inventory for project %s with %d tests",
        project_id,
        len(seen_ids),
    )


def list_runnable_tests(
    conn: sqlite3.Connection,
    project_id: str,
) -> list[DiscoveredTest]:
    """Return non-excluded tests in deterministic order."""

    rows = conn.execute(
        """
        SELECT
            id,
            title,
            spec_file,
            project_name,
            line,
            raw_list_line
        FROM tests
        WHERE project_id = ?
          AND excluded = 0
          AND is_stale = 0
        ORDER BY spec_file, line IS NULL, line, title, id
        """,
        (project_id,),
    ).fetchall()
    return [_row_to_discovered_test(row) for row in rows]


def ensure_state_layout(config: EffectiveConfig) -> None:
    """Create runtime state directories under the project state dir."""

    for name in ("runs", "work"):
        (config.state_dir / name).mkdir(parents=True, exist_ok=True)


def discover_inventory(config: EffectiveConfig) -> DiscoveryCounts:
    """Run Playwright list, parse output, and refresh the state database."""

    ensure_state_layout(config)
    db_path = database_path(config)
    output = run_playwright_list(config)
    inventory = parse_playwright_list(output, config.project_id)
    conn = ensure_database(db_path)
    with transaction(conn):
        refresh_inventory(conn, config, inventory)
    discovered = len(inventory.tests)
    excluded = sum(
        1
        for test in inventory.tests
        if apply_excludes((test,), config.exclude).get(test.id)
    )
    runnable = len(list_runnable_tests(conn, config.project_id))
    stale = conn.execute(
        """
        SELECT COUNT(*)
        FROM tests
        WHERE project_id = ? AND is_stale = 1
        """,
        (config.project_id,),
    ).fetchone()[0]
    conn.close()
    return DiscoveryCounts(
        discovered=discovered,
        runnable=runnable,
        excluded=excluded,
        stale=int(stale),
    )


def load_inventory_from_output(
    conn: sqlite3.Connection,
    config: EffectiveConfig,
    output: str,
) -> DiscoveryCounts:
    """Parse pre-captured list output and refresh the database."""

    inventory = parse_playwright_list(output, config.project_id)
    with transaction(conn):
        refresh_inventory(conn, config, inventory)
    discovered = len(inventory.tests)
    excluded = conn.execute(
        """
        SELECT COUNT(*)
        FROM tests
        WHERE project_id = ? AND excluded = 1 AND is_stale = 0
        """,
        (config.project_id,),
    ).fetchone()[0]
    runnable = len(list_runnable_tests(conn, config.project_id))
    stale = conn.execute(
        """
        SELECT COUNT(*)
        FROM tests
        WHERE project_id = ? AND is_stale = 1
        """,
        (config.project_id,),
    ).fetchone()[0]
    return DiscoveryCounts(
        discovered=discovered,
        runnable=runnable,
        excluded=int(excluded),
        stale=int(stale),
    )
