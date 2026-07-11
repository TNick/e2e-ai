"""Persist runtime command logs and state artifacts."""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from pathlib import Path

from .models import RuntimeState

logger = logging.getLogger(__name__)


def runtime_work_dir(state_dir: Path, run_id: str) -> Path:
    """Return the runtime artifact directory for one command run."""

    path = state_dir / "runs" / run_id / "runtime"
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_command_manifest(
    work_dir: Path,
    argv: Sequence[str],
    *,
    label: str,
) -> None:
    """Append a command manifest entry."""

    manifest_path = work_dir / "command-manifest.json"
    entries: list[dict[str, object]] = []
    if manifest_path.is_file():
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                entries = raw
        except json.JSONDecodeError:
            logger.log(
                1,
                "could not parse existing runtime command manifest at %s",
                manifest_path,
                exc_info=True,
            )
    entries.append({"label": label, "argv": list(argv)})
    manifest_path.write_text(
        json.dumps(entries, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_runtime_state(work_dir: Path, state: RuntimeState) -> None:
    """Persist runtime state metadata."""

    payload = {
        "id": state.id,
        "backend": state.backend,
        "started": state.started,
        "healthy": state.healthy,
        "cleanup_hint": state.cleanup_hint,
        "env_keys": sorted(state.env.keys()),
    }
    (work_dir / "state.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_compose_ps_output(work_dir: Path, output: str) -> None:
    """Write docker compose ps JSON output."""

    (work_dir / "docker-compose-ps.json").write_text(output, encoding="utf-8")


def append_runtime_log(work_dir: Path, name: str, text: str) -> None:
    """Append text to a named runtime log file."""

    path = work_dir / name
    with path.open("a", encoding="utf-8", errors="replace") as handle:
        handle.write(text)
        if not text.endswith("\n"):
            handle.write("\n")


def log_path(work_dir: Path, name: str) -> Path:
    """Return the path to a runtime log file."""

    return work_dir / name
