"""Target surface path resolution and scope helpers."""

from __future__ import annotations

from pathlib import Path

from .models import TargetConfig, TargetSurfaceConfig

VALID_TARGET_SCOPES = frozenset(
    {
        "frontend_only",
        "full_stack",
        "frontend_with_backend_reference",
    }
)


def resolve_surface_path(project_root: Path, surface_path: str) -> Path:
    """Resolve a configured surface path against the project root."""

    raw = Path(surface_path)
    if raw.is_absolute():
        return raw.resolve()
    return (project_root / raw).resolve()


def path_within_root(child: Path, root: Path) -> bool:
    """Return whether ``child`` is the same as or inside ``root``."""

    try:
        child.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return child.resolve() == root.resolve()


def _iter_surfaces(
    target: TargetConfig,
) -> list[tuple[str, TargetSurfaceConfig]]:
    return list(target.surfaces.items())


def has_editable_frontend(target: TargetConfig) -> bool:
    """Return whether at least one editable frontend surface exists."""

    for name, surface in _iter_surfaces(target):
        if name == "frontend" and surface.editable:
            return True
    return False


def has_editable_backend(target: TargetConfig) -> bool:
    """Return whether a backend surface is editable."""

    backend = target.surfaces.get("backend")
    return backend is not None and backend.editable


def has_backend_surface(target: TargetConfig) -> bool:
    """Return whether a backend surface is configured."""

    return "backend" in target.surfaces


def scope_flag_to_value(flag: str) -> str:
    """Convert CLI flag values to canonical config scope names."""

    mapping = {
        "frontend-only": "frontend_only",
        "full-stack": "full_stack",
        "frontend-with-backend-reference": "frontend_with_backend_reference",
    }
    return mapping.get(flag.replace("_", "-"), flag.replace("-", "_"))
