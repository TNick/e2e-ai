"""Patch artifacts and atomic, serialized application to the working tree.

Agents edit isolated worktrees and return a patch artifact; only the
orchestrator applies patches to the shared working tree, one at a time, under an
exclusive lock. This keeps test execution and planning parallel while the target
working tree stays single and coherent.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path

from attrs import define, field

from ..errors import E2eAiError


class PatchError(E2eAiError):
    """Raised when a patch cannot be created, validated, or applied."""


@define
class PatchArtifact:
    """Patch produced by an agent in an isolated workspace."""

    id: str = field()
    test_id: str = field()
    attempt_id: str = field()
    base_revision: str = field()
    path: Path = field()
    summary: str = field()
    changed_files: tuple[str, ...] = field(factory=tuple)


@define
class PatchApplyResult:
    """Result of applying one patch transaction."""

    patch_id: str = field()
    applied: bool = field()
    message: str = field()
    conflicts: tuple[str, ...] = field(factory=tuple)
    transaction_dir: Path | None = field(default=None)


def _git(args: Sequence[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _now() -> str:
    return datetime.now(tz=UTC).isoformat()


def create_patch_from_worktree(
    *,
    worktree: Path,
    output_path: Path,
    test_id: str = "",
    attempt_id: str = "",
) -> PatchArtifact:
    """Write a binary-safe patch from an isolated worktree."""

    head = _git(["rev-parse", "HEAD"], worktree)
    base_revision = head.stdout.strip() if head.returncode == 0 else "unknown"

    diff = _git(["diff", "--binary", "HEAD"], worktree)
    if diff.returncode != 0:
        raise PatchError(f"git diff failed: {diff.stderr.strip()}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(diff.stdout, encoding="utf-8")

    names = _git(["diff", "--name-only", "HEAD"], worktree)
    changed = tuple(line.strip() for line in names.stdout.splitlines() if line.strip())
    summary = f"{len(changed)} file(s) changed"
    return PatchArtifact(
        id=f"patch_{uuid.uuid4().hex[:12]}",
        test_id=test_id,
        attempt_id=attempt_id,
        base_revision=base_revision,
        path=output_path,
        summary=summary,
        changed_files=changed,
    )


def validate_patch_applies(
    *,
    project_root: Path,
    patch: PatchArtifact,
) -> None:
    """Run preflight checks before applying a patch (raises on failure)."""

    if not patch.path.is_file():
        raise PatchError(f"patch file missing: {patch.path}")
    if patch.path.stat().st_size == 0:
        return  # empty patch is a no-op
    check = _git(["apply", "--check", str(patch.path)], project_root)
    if check.returncode != 0:
        raise PatchError(f"patch does not apply cleanly: {check.stderr.strip()}")


@contextlib.contextmanager
def _exclusive_lock(lock_path: Path, timeout: float = 120.0) -> Iterator[None]:
    """Acquire an exclusive lock via O_CREAT|O_EXCL, releasing on exit."""

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    fd: int | None = None
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode("ascii"))
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise PatchError(
                    f"timed out acquiring working-tree lock at {lock_path}"
                ) from None
            time.sleep(0.1)
    try:
        yield
    finally:
        if fd is not None:
            os.close(fd)
        with contextlib.suppress(FileNotFoundError):
            lock_path.unlink()


def apply_patch_atomically(
    *,
    project_root: Path,
    patch: PatchArtifact,
    lock_dir: Path,
) -> PatchApplyResult:
    """Apply one patch to the project working tree under an exclusive lock."""

    lock_path = lock_dir / "working-tree.lock"
    with _exclusive_lock(lock_path):
        # Preflight: reject rather than corrupt the tree.
        try:
            validate_patch_applies(project_root=project_root, patch=patch)
        except PatchError as exc:
            return PatchApplyResult(
                patch_id=patch.id,
                applied=False,
                message=str(exc),
                conflicts=patch.changed_files,
            )

        transaction_dir = project_root / ".e2e-ai" / "patches" / patch.id
        transaction_dir.mkdir(parents=True, exist_ok=True)
        status = _git(["status", "--porcelain"], project_root)
        (transaction_dir / "pre-status.txt").write_text(status.stdout, encoding="utf-8")

        # Snapshot files the patch touches so we can restore on failure.
        snapshot = transaction_dir / "snapshot"
        for rel in patch.changed_files:
            src = project_root / rel
            if src.is_file():
                dst = snapshot / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)

        if patch.path.stat().st_size == 0:
            return PatchApplyResult(
                patch_id=patch.id,
                applied=True,
                message="empty patch (no-op)",
                transaction_dir=transaction_dir,
            )

        applied = _git(["apply", str(patch.path)], project_root)
        if applied.returncode != 0:
            _restore_snapshot(snapshot, project_root, patch.changed_files)
            return PatchApplyResult(
                patch_id=patch.id,
                applied=False,
                message=f"git apply failed: {applied.stderr.strip()}",
                conflicts=patch.changed_files,
                transaction_dir=transaction_dir,
            )

        diff_check = _git(["diff", "--check"], project_root)
        manifest = {
            "patch_id": patch.id,
            "test_id": patch.test_id,
            "attempt_id": patch.attempt_id,
            "base_revision": patch.base_revision,
            "changed_files": list(patch.changed_files),
            "applied_at": _now(),
            "diff_check_ok": diff_check.returncode == 0,
        }
        (transaction_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        return PatchApplyResult(
            patch_id=patch.id,
            applied=True,
            message="applied",
            transaction_dir=transaction_dir,
        )


def _restore_snapshot(
    snapshot: Path,
    project_root: Path,
    changed_files: Sequence[str],
) -> None:
    """Restore only the files listed in the patch transaction (no hard reset)."""

    for rel in changed_files:
        saved = snapshot / rel
        target = project_root / rel
        if saved.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(saved, target)
