"""Tests for live agent log tailing and progress formatting."""

from __future__ import annotations

import json
import time
from pathlib import Path

from e2e_ai.agents.progress import (
    LogTailFollower,
    format_stream_event,
    read_log_tail,
)


class TestReadLogTail:
    def test_returns_empty_when_missing(self, tmp_path: Path) -> None:
        assert read_log_tail(tmp_path / "missing.log") == ""

    def test_returns_tail_bytes(self, tmp_path: Path) -> None:
        path = tmp_path / "agent.log"
        path.write_text("alpha\nbeta\n", encoding="utf-8")
        assert "beta" in read_log_tail(path)


class TestFormatStreamEvent:
    def test_cursor_tool_call(self) -> None:
        line = json.dumps(
            {
                "type": "tool_call",
                "tool_call": {
                    "editToolCall": {"args": {"path": "src/foo.ts"}},
                },
            }
        )
        message = format_stream_event("cursor_auto", "implementer", line)
        assert message is not None
        assert "edit src/foo.ts" in message

    def test_codex_turn_completed(self) -> None:
        line = json.dumps({"type": "turn.completed"})
        message = format_stream_event("codex", "planner", line)
        assert message == "  [agent] planner/codex turn completed"

    def test_non_json_returns_none(self) -> None:
        assert format_stream_event("codex", "planner", "plain text") is None


class TestLogTailFollower:
    def test_emits_new_lines(self, tmp_path: Path) -> None:
        path = tmp_path / "stream.log"
        path.write_text("", encoding="utf-8")
        seen: list[str] = []

        follower = LogTailFollower(
            path,
            on_chunk=lambda chunk: None,
            line_handler=seen.append,
            poll_interval_seconds=0.05,
        )
        follower.start()
        try:
            with path.open("a", encoding="utf-8") as handle:
                handle.write('{"type":"thinking"}\n')
                handle.flush()
            deadline = time.time() + 2.0
            while time.time() < deadline and not seen:
                time.sleep(0.05)
        finally:
            follower.stop()
        assert seen
