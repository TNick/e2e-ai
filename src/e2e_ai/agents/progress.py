"""Live agent log tailing and stream-json progress formatting."""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_MAX_TAIL_BYTES = 200_000
DEFAULT_POLL_INTERVAL_SECONDS = 0.5


def read_log_tail(
    path: Path | str, *, max_bytes: int = DEFAULT_MAX_TAIL_BYTES
) -> str:
    """Return the trailing bytes of a log file as UTF-8 text."""

    log_path = Path(path)
    if not log_path.is_file():
        return ""
    data = log_path.read_bytes()
    if len(data) > max_bytes:
        data = data[-max_bytes:]
    return data.decode("utf-8", errors="replace")


def format_stream_event(
    agent_id: str,
    role: str,
    line: str,
) -> str | None:
    """Format one JSONL stream line as a concise progress message."""

    stripped = line.strip()
    if not stripped or not stripped.startswith("{"):
        return None
    try:
        event = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(event, dict):
        return None

    prefix = f"  [agent] {role}/{agent_id}"
    event_type = event.get("type")
    if not isinstance(event_type, str):
        return None

    if event_type == "tool_call":
        tool_call = event.get("tool_call")
        if isinstance(tool_call, dict):
            name = _cursor_tool_name(tool_call)
            if name:
                return f"{prefix} tool: {name}"
        return f"{prefix} tool_call"

    if event_type == "thinking":
        return f"{prefix} thinking"

    if event_type == "assistant":
        return f"{prefix} assistant"

    if event_type == "result":
        return f"{prefix} result"

    if event_type.startswith("turn."):
        return f"{prefix} {event_type.replace('.', ' ')}"

    if event_type.startswith("item."):
        item = event.get("item")
        if isinstance(item, dict):
            item_type = item.get("type")
            if item_type == "command_execution":
                cmd = item.get("command")
                if isinstance(cmd, str) and cmd.strip():
                    return f"{prefix} command: {cmd.strip()}"
            if item_type == "agent_message":
                return f"{prefix} message"
        return f"{prefix} {event_type.replace('.', ' ')}"

    if event_type == "thread.started":
        return f"{prefix} thread started"

    return None


def _cursor_tool_name(tool_call: dict[str, object]) -> str | None:
    for key in (
        "editToolCall",
        "shellToolCall",
        "readToolCall",
        "grepToolCall",
    ):
        payload = tool_call.get(key)
        if not isinstance(payload, dict):
            continue
        args = payload.get("args")
        if not isinstance(args, dict):
            return key.replace("ToolCall", "")
        path = args.get("path")
        if isinstance(path, str) and path.strip():
            return f"{key.replace('ToolCall', '')} {path.strip()}"
        command = args.get("command")
        if isinstance(command, str) and command.strip():
            return f"shell {command.strip()}"
        return key.replace("ToolCall", "")
    return None


class LogTailFollower:
    """Poll a growing log file and emit newly appended text."""

    def __init__(
        self,
        path: Path | str,
        on_chunk: Callable[[str], None],
        *,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
        line_handler: Callable[[str], None] | None = None,
    ) -> None:
        self._path = Path(path)
        self._on_chunk = on_chunk
        self._poll_interval_seconds = poll_interval_seconds
        self._line_handler = line_handler
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._offset = 0
        self._line_buffer = ""

    def start(self) -> None:
        """Start background tail polling."""

        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run,
            name="e2e-ai-log-tail",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop background tail polling."""

        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        self._flush_lines()

    def _run(self) -> None:
        while not self._stop.is_set():
            self._poll_once()
            time.sleep(self._poll_interval_seconds)
        self._poll_once()

    def _poll_once(self) -> None:
        if not self._path.is_file():
            return
        try:
            size = self._path.stat().st_size
        except OSError as exc:
            logger.log(1, "log tail stat failed for %s: %s", self._path, exc)
            return
        if size < self._offset:
            self._offset = 0
            self._line_buffer = ""
        if size == self._offset:
            return
        try:
            with self._path.open("rb") as handle:
                handle.seek(self._offset)
                chunk = handle.read(size - self._offset)
        except OSError as exc:
            logger.log(1, "log tail read failed for %s: %s", self._path, exc)
            return
        self._offset = size
        if not chunk:
            return
        text = chunk.decode("utf-8", errors="replace")
        self._on_chunk(text)
        if self._line_handler is not None:
            self._feed_lines(text)

    def _feed_lines(self, text: str) -> None:
        self._line_buffer += text
        while True:
            newline = self._line_buffer.find("\n")
            if newline < 0:
                break
            line = self._line_buffer[:newline]
            self._line_buffer = self._line_buffer[newline + 1 :]
            if line.strip():
                self._line_handler(line)

    def _flush_lines(self) -> None:
        if self._line_handler is None:
            return
        if self._line_buffer.strip():
            self._line_handler(self._line_buffer)
        self._line_buffer = ""
