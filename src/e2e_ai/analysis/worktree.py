"""Git working-tree snapshots for implementer change detection."""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorktreeEntry:
    """One path's porcelain status and content fingerprint."""

    status: str
    fingerprint: str | None


@dataclass(frozen=True)
class WorktreeSnapshot:
    """Captured git working-tree state for one project root."""

    entries: dict[str, WorktreeEntry]


def _normalize_path(path: str) -> str:
    return path.replace("\\", "/")


def _fingerprint(path: Path) -> str | None:
    if not path.is_file():
        return None
    stat = path.stat()
    return f"{stat.st_mtime_ns}:{stat.st_size}"


def capture_worktree_snapshot(project_root: Path) -> WorktreeSnapshot:
    """Capture porcelain status and fingerprints for changed paths."""

    entries: dict[str, WorktreeEntry] = {}
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "-uall"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.log(
            1,
            "could not capture git worktree snapshot: %s",
            exc,
            exc_info=True,
        )
        return WorktreeSnapshot(entries=entries)

    if result.returncode != 0:
        logger.log(
            1,
            "git status returned %d while capturing worktree snapshot",
            result.returncode,
        )
        return WorktreeSnapshot(entries=entries)

    for raw_line in result.stdout.splitlines():
        line = raw_line.rstrip()
        if len(line) < 4:
            continue
        status = line[:2]
        rel = _normalize_path(line[3:].strip())
        if not rel:
            continue
        file_path = project_root / rel
        entries[rel] = WorktreeEntry(
            status=status,
            fingerprint=_fingerprint(file_path),
        )
    return WorktreeSnapshot(entries=entries)


def diff_worktree_snapshots(
    before: WorktreeSnapshot,
    after: WorktreeSnapshot,
    project_root: Path,
) -> tuple[str, ...]:
    """Return project-relative paths that changed between snapshots."""

    changed: set[str] = set()
    all_paths = set(before.entries) | set(after.entries)
    for rel in sorted(all_paths):
        prior = before.entries.get(rel)
        current = after.entries.get(rel)
        if prior is None and current is None:
            continue
        if prior is None or current is None:
            changed.add(rel)
            continue
        if prior.status != current.status:
            changed.add(rel)
            continue
        if prior.fingerprint != current.fingerprint:
            changed.add(rel)
            continue
        if current.fingerprint is None and current.status.strip():
            file_path = project_root / rel
            if file_path.exists():
                changed.add(rel)
    return tuple(sorted(changed))
