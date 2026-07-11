"""fr-two project detection, default config, and validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ...config import EffectiveConfig
from ...errors import ConfigError

# Signature files that identify a checkout as the fr-two repository.
_FR_TWO_MARKERS = (
    Path("e2e") / "helpers" / "dockerDb.ts",
    Path("scripts") / "e2e_agent_fix_loop.py",
)


def is_fr_two_project(project_root: Path) -> bool:
    """Return whether ``project_root`` is the fr-two repository.

    A checkout counts as fr-two when its project config declares
    ``project.id: fr-two`` or when the fr-two signature files are present.
    """

    config_file = project_root / "e2e-ai.yml"
    if config_file.is_file():
        try:
            data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            data = None
        if isinstance(data, dict):
            project = data.get("project")
            if isinstance(project, dict) and str(project.get("id")) == "fr-two":
                return True
    return all((project_root / marker).exists() for marker in _FR_TWO_MARKERS)


def default_fr_two_config() -> dict[str, Any]:
    """Return the documented default config for fr-two.

    This mirrors ``examples/fr-two.e2e-ai.yml`` and is the reference for the
    fr-two isolation/storage contract.
    """

    return {
        "project": {"id": "fr-two"},
        "state": {"dir": ".e2e-ai"},
        "playwright": {
            "cwd": "e2e",
            "list_command": ["pnpm", "exec", "playwright", "test", "--list"],
            "run_command": ["pnpm", "exec", "playwright", "test"],
            "report_env": {
                "json": "PLAYWRIGHT_JSON_OUTPUT_NAME",
                "blob": "PLAYWRIGHT_BLOB_OUTPUT_FILE",
            },
            "base_url_env": "PLAYWRIGHT_BASE_URL",
            "api_base_env": "PLAYWRIGHT_API_BASE",
            "lab_flag_env": "E2E_LAB_COMPOSE",
        },
        "exclude": {"tests": [r"tests/_diag-.*\.spec\.ts"]},
        "isolation": {
            "backend": "fr_two",
            "keep_on_failure": True,
            "keep_on_success": False,
            "slots": {
                "count": 4,
                "database_prefix": "frtwo_e2e_slot",
                "database_user": "frtwo",
            },
            "storage": {
                "wipe_before_attempt": True,
                "targets": [
                    {
                        "kind": "directory",
                        "name": "uploads",
                        "path": "playground/e2e/slots/{slot_id}/uploads",
                    },
                    {
                        "kind": "directory",
                        "name": "backend-data",
                        "path": "playground/e2e/slots/{slot_id}/backend-data",
                    },
                    {
                        "kind": "directory",
                        "name": "map-renderer",
                        "path": "playground/e2e/slots/{slot_id}/map-renderer",
                    },
                    {
                        "kind": "directory",
                        "name": "mapproxy-cache",
                        "path": "playground/e2e/slots/{slot_id}/mapproxy-cache",
                    },
                    {
                        "kind": "minio",
                        "name": "lab-bucket",
                        "bucket": "frtwo-lab",
                        "prefix": "e2e/{slot_id}/",
                    },
                ],
            },
        },
        "full_verification": {"command": ["e2e-ai", "verify"]},
        "agents": {
            "planner": {"plugin": "codex", "profile": "difficult"},
            "implementer": {"plugin": "codex", "profile": "normal"},
            "instrumenter": {"plugin": "codex", "profile": "difficult"},
        },
    }


def load_fr_two_raw(config: EffectiveConfig) -> dict[str, Any]:
    """Return the raw project YAML for fr-two, or the documented default.

    fr-two-specific ``isolation.slots`` / ``isolation.storage`` are not part of
    the typed :class:`EffectiveConfig`, so the adapter reads them straight from
    the project config file (the adapter owns these project-specific details).
    """

    path = config.project_config_path
    if path is not None and Path(path).is_file():
        try:
            data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:  # pragma: no cover - passthrough
            raise ConfigError(f"invalid fr-two config {path}: {exc}") from exc
        if isinstance(data, dict):
            return data
    return default_fr_two_config()


def fr_two_isolation_section(config: EffectiveConfig) -> dict[str, Any]:
    """Return the fr-two ``isolation`` section (raw), falling back to defaults."""

    raw = load_fr_two_raw(config)
    isolation = raw.get("isolation")
    if not isinstance(isolation, dict):
        return default_fr_two_config()["isolation"]
    return isolation


def validate_fr_two_config(config: EffectiveConfig) -> None:
    """Validate fr-two-specific configuration (raises :class:`ConfigError`)."""

    if config.isolation.backend != "fr_two":
        raise ConfigError(
            "fr-two adapter requires isolation.backend: fr_two, got "
            f"{config.isolation.backend!r}"
        )
    isolation = fr_two_isolation_section(config)
    slots = isolation.get("slots")
    if not isinstance(slots, dict):
        raise ConfigError("fr-two config requires isolation.slots")
    count = slots.get("count")
    if not isinstance(count, int) or count < 1:
        raise ConfigError("isolation.slots.count must be a positive integer")
    if not slots.get("database_prefix"):
        raise ConfigError("isolation.slots.database_prefix is required")
    if not slots.get("database_user"):
        raise ConfigError("isolation.slots.database_user is required")
