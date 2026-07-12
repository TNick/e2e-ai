"""Tests for git worktree snapshot diffing."""

from __future__ import annotations

import subprocess
from pathlib import Path

from e2e_ai.analysis.worktree import (
    capture_worktree_snapshot,
    diff_worktree_snapshots,
)


def _init_git_repo(root: Path) -> None:
    subprocess.run(
        ["git", "init"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "e2e-ai@test"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "e2e-ai"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )


class TestWorktreeSnapshots:
    def test_diff_ignores_preexisting_dirty_file(self, tmp_path: Path) -> None:
        _init_git_repo(tmp_path)
        dirty = tmp_path / "frontend" / "existing.ts"
        dirty.parent.mkdir(parents=True)
        dirty.write_text("unchanged\n", encoding="utf-8")
        before = capture_worktree_snapshot(tmp_path)
        after = capture_worktree_snapshot(tmp_path)
        assert diff_worktree_snapshots(before, after, tmp_path) == ()

    def test_diff_detects_new_modification(self, tmp_path: Path) -> None:
        _init_git_repo(tmp_path)
        target = tmp_path / "frontend" / "app.tsx"
        target.parent.mkdir(parents=True)
        before = capture_worktree_snapshot(tmp_path)
        target.write_text("new code\n", encoding="utf-8")
        after = capture_worktree_snapshot(tmp_path)
        assert "frontend/app.tsx" in diff_worktree_snapshots(
            before,
            after,
            tmp_path,
        )

    def test_diff_detects_fingerprint_change(self, tmp_path: Path) -> None:
        _init_git_repo(tmp_path)
        target = tmp_path / "backend" / "api.py"
        target.parent.mkdir(parents=True)
        target.write_text("v1\n", encoding="utf-8")
        before = capture_worktree_snapshot(tmp_path)
        target.write_text("v2 with more content\n", encoding="utf-8")
        after = capture_worktree_snapshot(tmp_path)
        changed = diff_worktree_snapshots(before, after, tmp_path)
        assert "backend/api.py" in changed
