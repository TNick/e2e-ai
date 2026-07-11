"""Tests for Playwright inventory parsing and persistence."""

from __future__ import annotations

import json
from pathlib import Path

from e2e_ai.config.models import (
    CommandSpec,
    EffectiveConfig,
    IsolationConfig,
    PlaywrightConfig,
    RepairPolicy,
    RoutingConfig,
)
from e2e_ai.db.migrations import ensure_database
from e2e_ai.inventory.models import DiscoveredTest
from e2e_ai.inventory.models import TestInventory as InventorySnapshot
from e2e_ai.inventory.playwright_list import build_test_id, parse_playwright_list
from e2e_ai.inventory.store import (
    apply_excludes,
    list_runnable_tests,
    load_inventory_from_output,
    refresh_inventory,
)

SAMPLE_JSON = {
    "suites": [
        {
            "title": "login.spec.ts",
            "file": "login.spec.ts",
            "specs": [
                {
                    "title": "logs in",
                    "file": "login.spec.ts",
                    "line": 10,
                    "column": 3,
                    "tests": [
                        {"projectName": "chromium"},
                        {"projectName": "firefox"},
                    ],
                }
            ],
            "suites": [
                {
                    "title": "admin",
                    "file": "login.spec.ts",
                    "specs": [
                        {
                            "title": "sees dashboard",
                            "file": "login.spec.ts",
                            "line": 20,
                            "column": 5,
                            "tests": [{"projectName": "chromium"}],
                        }
                    ],
                }
            ],
        },
        {
            "title": "flaky.spec.ts",
            "file": "flaky.spec.ts",
            "specs": [
                {
                    "title": "sometimes",
                    "file": "flaky.spec.ts",
                    "line": 4,
                    "column": 1,
                    "tests": [{"projectName": "chromium"}],
                }
            ],
        },
    ]
}

SAMPLE_TEXT = """\
# sample text inventory
[chromium] › login.spec.ts › logs in
[firefox] › login.spec.ts › logs in
[chromium] › login.spec.ts › admin › sees dashboard
[chromium] › flaky.spec.ts › sometimes
"""


def _effective_config(
    tmp_path: Path,
    *,
    exclude: tuple[str, ...] = (),
) -> EffectiveConfig:
    return EffectiveConfig(
        project_id="demo-proj",
        project_root=tmp_path,
        state_dir=tmp_path / ".e2e-ai",
        playwright=PlaywrightConfig(
            cwd=".",
            list_command=CommandSpec(
                argv=("pnpm", "exec", "playwright", "test", "--list")
            ),
            run_command=CommandSpec(argv=("pnpm", "exec", "playwright", "test")),
        ),
        agents=(),
        isolation=IsolationConfig(),
        exclude=exclude,
        repair_policy=RepairPolicy(),
        routing=RoutingConfig(),
    )


class TestInventoryParser:
    """Playwright list parsing."""

    def test_parses_basic_playwright_list_output(self) -> None:
        inventory = parse_playwright_list(
            json.dumps(SAMPLE_JSON),
            "demo-proj",
        )
        assert len(inventory.tests) == 4
        titles = sorted(test.title for test in inventory.tests)
        assert titles == [
            "admin › sees dashboard",
            "logs in",
            "logs in",
            "sometimes",
        ]
        chromium_login = next(
            test
            for test in inventory.tests
            if test.title == "logs in" and test.project_name == "chromium"
        )
        assert chromium_login.spec_file == "login.spec.ts"
        assert chromium_login.line == 10
        assert chromium_login.raw_list_line == ("[chromium] › login.spec.ts › logs in")

    def test_parses_text_list_output(self) -> None:
        inventory = parse_playwright_list(SAMPLE_TEXT, "demo-proj")
        assert len(inventory.tests) == 4

    def test_build_test_id_is_stable(self) -> None:
        first = build_test_id(
            "demo-proj",
            "login.spec.ts",
            "logs in",
            "chromium",
        )
        second = build_test_id(
            "demo-proj",
            "login.spec.ts",
            "logs in",
            "chromium",
        )
        different_line = build_test_id(
            "demo-proj",
            "login.spec.ts",
            "logs in",
            "firefox",
        )
        assert first == second
        assert first != different_line
        assert first.startswith("demo-proj_")
        assert len(first.split("_", 1)[1]) == 12

    def test_build_test_id_ignores_line_metadata(self) -> None:
        inventory = parse_playwright_list(
            json.dumps(SAMPLE_JSON),
            "demo-proj",
        )
        login_tests = [test for test in inventory.tests if test.title == "logs in"]
        assert len({test.id for test in login_tests}) == 2


class TestInventoryStore:
    """Inventory persistence and querying."""

    def test_refresh_inventory_upserts_tests(self, tmp_path: Path) -> None:
        config = _effective_config(tmp_path)
        conn = ensure_database(tmp_path / "state.sqlite3")
        inventory = parse_playwright_list(json.dumps(SAMPLE_JSON), config.project_id)
        refresh_inventory(conn, config, inventory)
        conn.commit()

        row = conn.execute(
            "SELECT COUNT(*) FROM tests WHERE project_id = ? AND is_stale = 0",
            (config.project_id,),
        ).fetchone()
        assert row is not None and row[0] == 4

        first_seen = conn.execute(
            "SELECT first_seen_at FROM tests WHERE project_id = ? LIMIT 1",
            (config.project_id,),
        ).fetchone()[0]

        smaller_inventory = InventorySnapshot(tests=inventory.tests[:2])
        refresh_inventory(conn, config, smaller_inventory)
        conn.commit()

        stale_count = conn.execute(
            "SELECT COUNT(*) FROM tests WHERE project_id = ? AND is_stale = 1",
            (config.project_id,),
        ).fetchone()[0]
        assert stale_count == 2

        active_count = conn.execute(
            "SELECT COUNT(*) FROM tests WHERE project_id = ? AND is_stale = 0",
            (config.project_id,),
        ).fetchone()[0]
        assert active_count == 2

        same_first_seen = conn.execute(
            """
            SELECT first_seen_at
            FROM tests
            WHERE project_id = ? AND is_stale = 0
            LIMIT 1
            """,
            (config.project_id,),
        ).fetchone()[0]
        assert same_first_seen == first_seen
        conn.close()

    def test_exclude_patterns_mark_tests_excluded(self, tmp_path: Path) -> None:
        config = _effective_config(tmp_path, exclude=(r"flaky\.spec\.ts",))
        inventory = parse_playwright_list(json.dumps(SAMPLE_JSON), config.project_id)
        reasons = apply_excludes(inventory.tests, config.exclude)
        flaky = next(test for test in inventory.tests if "flaky" in test.spec_file)
        assert reasons[flaky.id] is not None
        assert all(
            reasons[test.id] is None
            for test in inventory.tests
            if "flaky" not in test.spec_file
        )

        conn = ensure_database(tmp_path / "state.sqlite3")
        load_inventory_from_output(conn, config, json.dumps(SAMPLE_JSON))
        excluded = conn.execute(
            """
            SELECT COUNT(*)
            FROM tests
            WHERE project_id = ? AND excluded = 1 AND is_stale = 0
            """,
            (config.project_id,),
        ).fetchone()[0]
        assert excluded == 1
        conn.close()

    def test_runnable_tests_are_deterministic(self, tmp_path: Path) -> None:
        config = _effective_config(tmp_path, exclude=(r"flaky\.spec\.ts",))
        conn = ensure_database(tmp_path / "state.sqlite3")
        load_inventory_from_output(conn, config, json.dumps(SAMPLE_JSON))

        first = list_runnable_tests(conn, config.project_id)
        second = list_runnable_tests(conn, config.project_id)
        assert first == second
        assert len(first) == 3
        assert all(isinstance(test, DiscoveredTest) for test in first)
        assert [test.spec_file for test in first] == sorted(
            test.spec_file for test in first
        )
        conn.close()

    def test_load_inventory_from_output_counts(self, tmp_path: Path) -> None:
        config = _effective_config(tmp_path, exclude=(r"flaky\.spec\.ts",))
        conn = ensure_database(tmp_path / "state.sqlite3")
        counts = load_inventory_from_output(
            conn,
            config,
            json.dumps(SAMPLE_JSON),
        )
        assert counts.discovered == 4
        assert counts.runnable == 3
        assert counts.excluded == 1
        assert counts.stale == 0
        conn.close()
