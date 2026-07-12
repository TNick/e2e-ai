"""Tests for the failure-context and instrumentation layer."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from e2e_ai.analysis.context import (
    build_logical_key,
    build_repair_context,
    build_test_list_selector,
    build_variant_key,
    load_previous_plans,
)
from e2e_ai.analysis.failure_packet import FailurePacket, build_failure_packet
from e2e_ai.analysis.instrumentation import should_instrument
from e2e_ai.analysis.signatures import (
    build_failure_signature,
    detect_generic_family,
)
from e2e_ai.analysis.store import insert_failure_packet
from e2e_ai.analysis.text import strip_ansi
from e2e_ai.db.migrations import ensure_database
from e2e_ai.inventory.models import DiscoveredTest
from e2e_ai.runner.models import TestRunResult as RunResult


def _report(with_attachments: bool = True) -> dict:
    result = {
        "status": "failed",
        "errors": [
            {
                "message": (
                    "Error: expect(received).toBe(expected)\n"
                    "  Expected: 1\n  Received: 2"
                ),
                "stack": "at a.spec.ts:5:5",
                "location": {"file": "a.spec.ts", "line": 5, "column": 5},
            }
        ],
        "attachments": [],
    }
    if with_attachments:
        result["attachments"] = [
            {
                "name": "screenshot",
                "contentType": "image/png",
                "path": "test-failed-1.png",
            },
            {
                "name": "trace",
                "contentType": "application/zip",
                "path": "trace.zip",
            },
        ]
    return {"suites": [{"specs": [{"tests": [{"results": [result]}]}]}]}


def _test() -> DiscoveredTest:
    return DiscoveredTest(
        id="demo_x",
        title="admin › does a thing",
        spec_file="a.spec.ts",
        project_name="chromium",
        line=5,
    )


def _attempt(work: Path, log_text: str) -> RunResult:
    work.mkdir(parents=True, exist_ok=True)
    log = work / "output.log"
    log.write_text(log_text, encoding="utf-8")
    return RunResult(
        attempt_id="att-1",
        test_id="demo_x",
        status="failed",
        exit_code=1,
        duration_seconds=0.2,
        stdout_path=log,
        stderr_path=log,
        json_report_path=work / "playwright-results.json",
        work_dir=work,
    )


class TestText:
    def test_strip_ansi(self):
        assert strip_ansi("\x1b[31mred\x1b[0m text") == "red text"


class TestSignatures:
    def test_same_error_has_same_signature(self):
        base = dict(
            id="",
            test_id="t",
            attempt_id="a",
            signature="",
            spec_file="a.spec.ts",
            test_title="x",
            error_message="Error: boom at 2024-01-01T10:00:00Z port :54321",
            stack="",
            stdout_tail="",
            stderr_tail="",
            suspected_family="assertion",
        )
        p1 = FailurePacket(**base)
        # Same error, different volatile timestamp/port and different attempt.
        base2 = dict(base)
        base2["attempt_id"] = "b"
        base2["error_message"] = (
            "Error: boom at 2024-06-30T22:00:00Z port :12345"
        )
        p2 = FailurePacket(**base2)
        assert build_failure_signature(p1) == build_failure_signature(p2)

    def test_detects_assertion_family(self):
        assert (
            detect_generic_family(
                "a.spec.ts", "expect(received).toBe(expected)", ""
            )
            == "assertion"
        )


class TestFailurePacket:
    def test_builds_packet_from_failed_report(self, tmp_path):
        packet = build_failure_packet(
            test=_test(),
            attempt=_attempt(tmp_path / "att", "log\nline"),
            report=_report(),
            lease=None,
        )
        assert packet.test_id == "demo_x"
        assert packet.attempt_id == "att-1"
        assert packet.id.startswith("fp_")
        assert packet.signature
        assert packet.suspected_family == "assertion"
        assert len(packet.screenshot_paths) == 1
        assert len(packet.trace_paths) == 1
        assert "Expected" in packet.error_message

    def test_extracts_stdout_stderr_tails(self, tmp_path):
        log_text = "\n".join(f"log line {i}" for i in range(200))
        packet = build_failure_packet(
            test=_test(),
            attempt=_attempt(tmp_path / "att", log_text),
            report=_report(with_attachments=False),
            lease=None,
        )
        assert "log line 199" in packet.stdout_tail
        # Combined log: stderr tail stays empty.
        assert packet.stderr_tail == ""


class TestAttachments:
    def test_extracts_screenshot_and_trace_paths(self, tmp_path):
        from e2e_ai.analysis.attachments import extract_attachment_paths

        shots, traces = extract_attachment_paths(_report(), tmp_path)
        assert [p.name for p in shots] == ["test-failed-1.png"]
        assert [p.name for p in traces] == ["trace.zip"]
        assert all(p.is_absolute() for p in [*shots, *traces])


# ── DB-backed context / instrumentation ─────────────────────────────────────
def _seed_db(tmp_path: Path) -> sqlite3.Connection:
    conn = ensure_database(tmp_path / "state.sqlite3")
    now = datetime.now(tz=UTC).isoformat()
    conn.execute(
        "INSERT INTO projects (id, root_path, config_hash, created_at,"
        " updated_at) VALUES ('p','/r','h',?,?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO tests (id, project_id, title, spec_file, raw_list_line,"
        " excluded, first_seen_at, last_seen_at) VALUES"
        " ('demo_x','p','t','a.spec.ts','a',0,?,?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO runs (id, project_id, started_at, status) VALUES"
        " ('run1','p',?, 'running')",
        (now,),
    )
    conn.execute(
        "INSERT INTO attempts (id, run_id, test_id, attempt_index, status,"
        " work_dir, started_at) VALUES ('att1','run1','demo_x',0,'failed',"
        "'w',?)",
        (now,),
    )
    conn.commit()
    return conn


def _insert_plan(conn, plan_id, packet_id, text, created_at):
    conn.execute(
        "INSERT INTO repair_plans (id, test_id, failure_packet_id, agent_id,"
        " plan_text, created_at) VALUES (?,?,?,?,?,?)",
        (plan_id, "demo_x", packet_id, "codex", text, created_at),
    )
    conn.commit()


def _packet(pid: str) -> FailurePacket:
    return FailurePacket(
        id=pid,
        test_id="demo_x",
        attempt_id="att1",
        signature="sig",
        spec_file="a.spec.ts",
        test_title="t",
        error_message="boom",
        stack="",
        stdout_tail="",
        stderr_tail="",
        suspected_family="assertion",
    )


class TestContext:
    def test_builds_normalized_identity_fields(self, tmp_path):
        conn = _seed_db(tmp_path)
        packet = _packet("fp_1")
        insert_failure_packet(conn, packet)
        context = build_repair_context(conn, packet, test=_test())
        assert context.logical_key == build_logical_key(
            "a.spec.ts", "admin › does a thing"
        )
        assert context.variant_key == build_variant_key(
            "a.spec.ts", "admin › does a thing", "chromium"
        )
        assert context.test_list_selector == build_test_list_selector(
            spec_file="a.spec.ts",
            title="admin › does a thing",
            project_name="chromium",
            raw_list_line="",
        )
        conn.close()

    def test_previous_failed_plans_are_loaded_in_order(self, tmp_path):
        conn = _seed_db(tmp_path)
        insert_failure_packet(conn, _packet("fp_1"))
        _insert_plan(
            conn, "plan1", "fp_1", "first plan", "2024-01-01T00:00:00Z"
        )
        _insert_plan(
            conn, "plan2", "fp_1", "second plan", "2024-01-02T00:00:00Z"
        )
        plans = load_previous_plans(conn, "demo_x")
        assert plans == ["first plan", "second plan"]
        conn.close()


class TestInstrumentation:
    def test_second_failure_requests_instrumentation(self, tmp_path):
        conn = _seed_db(tmp_path)
        # First failure: no prior plan -> do not instrument yet.
        assert should_instrument(conn, "demo_x", "sig") is False
        insert_failure_packet(conn, _packet("fp_1"))
        _insert_plan(
            conn, "plan1", "fp_1", "first plan", "2024-01-01T00:00:00Z"
        )
        # After a fix was attempted, the next failure should request it.
        assert should_instrument(conn, "demo_x", "sig") is True
        conn.close()
