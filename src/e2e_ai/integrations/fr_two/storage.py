"""Wipe fr-two slot file storage and MinIO object storage before reuse."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ...isolation.models import IsolationContext

# The type is imported lazily to avoid a cycle with isolation.py at import time.


def _resolve(project_root: Path, raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else (project_root / path)


def wipe_fr_two_storage(
    context: IsolationContext,
    slot,  # FrTwoSlot (untyped to avoid an import cycle)
    targets: Sequence[Mapping[str, Any]] | None = None,
) -> None:
    """Wipe uploads, cache, renderer, backend, and object storage for a slot.

    Directory targets are emptied (recreated clean); MinIO targets are cleared
    by prefix. ``{slot_id}`` in a target path/prefix is substituted with the
    slot id.
    """

    if targets is None:
        from .config import fr_two_isolation_section

        isolation = fr_two_isolation_section(context.config)
        targets = isolation.get("storage", {}).get("targets", [])

    for target in targets:
        kind = str(target.get("kind", "directory"))
        if kind == "directory":
            raw = str(target.get("path", "")).format(slot_id=slot.id)
            if not raw:
                continue
            path = _resolve(context.project_root, raw)
            if path.exists():
                shutil.rmtree(path, ignore_errors=True)
            path.mkdir(parents=True, exist_ok=True)
        elif kind == "minio":
            bucket = str(target.get("bucket", ""))
            prefix = str(target.get("prefix", "")).format(slot_id=slot.id)
            if bucket and prefix:
                reset_fr_two_minio_prefix(context, bucket, prefix)


def build_minio_wipe_request(
    bucket: str,
    prefix: str,
    *,
    alias: str = "e2e",
    endpoint: str | None = None,
) -> dict[str, Any]:
    """Build a structured MinIO removal request (no side effects).

    Returns the ``mc`` command plus its fields so callers/tests can inspect it
    before anything is executed.
    """

    argv = ["mc", "rm", "--recursive", "--force", f"{alias}/{bucket}/{prefix}"]
    return {
        "tool": "mc",
        "alias": alias,
        "bucket": bucket,
        "prefix": prefix,
        "endpoint": endpoint,
        "argv": argv,
    }


def reset_fr_two_minio_prefix(
    context: IsolationContext,
    bucket: str,
    prefix: str,
) -> None:
    """Remove MinIO objects for one fr-two slot (best effort)."""

    if shutil.which("mc") is None:
        return
    request = build_minio_wipe_request(bucket, prefix)
    subprocess.run(
        request["argv"],
        cwd=str(context.project_root),
        capture_output=True,
        text=True,
        check=False,
    )
