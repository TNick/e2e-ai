"""Render fr-two per-slot Docker Compose service overrides."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml

from ...isolation.models import IsolationContext

COMPOSE_OVERRIDE_NAME = "fr-two-compose.override.yml"


def render_fr_two_compose_override(
    context: IsolationContext,
    slots: Sequence,  # Sequence[FrTwoSlot]
) -> dict[str, Any]:
    """Render fr-two backend/frontend slot services for Docker Compose."""

    services: dict[str, Any] = {}
    for slot in slots:
        common_env = {
            "E2E_SLOT_ID": slot.id,
            "E2E_DATABASE_URL": slot.database_url(),
            "POSTGRES_DB": slot.database_name,
            "POSTGRES_USER": slot.database_user,
            "POSTGRES_PASSWORD": slot.database_password,
        }
        services[f"frtwo-backend-{slot.id}"] = {
            "extends": {"service": "backend"},
            "ports": [f"{slot.backend_port}:8000"],
            "environment": common_env,
        }
        services[f"frtwo-frontend-{slot.id}"] = {
            "extends": {"service": "frontend"},
            "ports": [f"{slot.frontend_port}:80"],
            "environment": {
                "E2E_SLOT_ID": slot.id,
                "PLAYWRIGHT_API_BASE": f"{slot.backend_url()}/api",
            },
        }
    return {"services": services}


def write_fr_two_compose_override(
    context: IsolationContext,
    slots: Sequence,  # Sequence[FrTwoSlot]
) -> Path:
    """Write the generated fr-two Compose override under the state dir."""

    override = render_fr_two_compose_override(context, slots)
    path = context.state_dir / COMPOSE_OVERRIDE_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(override, sort_keys=False), encoding="utf-8")
    return path
