"""Environment manifest for one e2e-ai-controlled fr-two run."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from attrs import asdict, define, field

from ...isolation.models import IsolationContext

MANIFEST_NAME = "fr-two-manifest.json"


@define
class FrTwoManifest:
    """Manifest describing the slots provisioned for one fr-two run."""

    project_id: str = field()
    created_at: str = field()
    slots: tuple[dict[str, Any], ...] = field(factory=tuple)

    def slot(self, slot_id: str) -> dict[str, Any] | None:
        return next((s for s in self.slots if s.get("id") == slot_id), None)


def _slot_dict(slot) -> dict[str, Any]:  # FrTwoSlot
    data = asdict(slot)
    data["root_dir"] = str(slot.root_dir)
    return data


def write_fr_two_manifest(
    context: IsolationContext,
    slots: Sequence,  # Sequence[FrTwoSlot]
) -> Path:
    """Write the fr-two environment manifest under the state dir."""

    manifest = FrTwoManifest(
        project_id=context.config.project_id,
        created_at=datetime.now(tz=UTC).isoformat(),
        slots=tuple(_slot_dict(slot) for slot in slots),
    )
    path = context.state_dir / MANIFEST_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(manifest), indent=2, default=str), encoding="utf-8"
    )
    return path


def load_fr_two_manifest(path: Path) -> FrTwoManifest:
    """Load a fr-two environment manifest."""

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return FrTwoManifest(
        project_id=str(data.get("project_id", "")),
        created_at=str(data.get("created_at", "")),
        slots=tuple(data.get("slots", []) or []),
    )
