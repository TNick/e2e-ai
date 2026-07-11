"""Heuristic detection of frontend/backend project layout."""

from __future__ import annotations

import logging
from pathlib import Path

from attrs import define, field

from .models import TargetConfig, TargetSurfaceConfig, default_target_config
from .target import VALID_TARGET_SCOPES

logger = logging.getLogger(__name__)

_FRONTEND_SIGNALS = (
    "package.json",
    "playwright.config.ts",
    "playwright.config.js",
    "playwright.config.mjs",
    "vite.config.ts",
    "vite.config.js",
    "next.config.js",
    "next.config.mjs",
    "next.config.ts",
)

_FRONTEND_DIRS = ("frontend", "web", "app", "pages", "src")
_BACKEND_DIRS = ("backend", "api", "server")
_MONOREPO_SIGNALS = (
    "pnpm-workspace.yaml",
    "turbo.json",
    "nx.json",
    "lerna.json",
)
_MONOREPO_DIRS = ("apps", "packages", "services")


@define
class TargetDetectionResult:
    """Outcome of inspecting a project directory for edit surfaces."""

    frontend_paths: tuple[str, ...] = field(factory=tuple)
    backend_paths: tuple[str, ...] = field(factory=tuple)
    suggested_scope: str = field(default="frontend_only")
    confidence: str = field(default="high")
    comments: tuple[str, ...] = field(factory=tuple)


def _exists_any(root: Path, names: tuple[str, ...]) -> bool:
    return any((root / name).exists() for name in names)


def _existing_dirs(root: Path, names: tuple[str, ...]) -> list[str]:
    found: list[str] = []
    for name in names:
        path = root / name
        if path.is_dir():
            found.append(name)
    return found


def _backend_file_signals(root: Path) -> bool:
    backend_files = (
        "pyproject.toml",
        "requirements.txt",
        "go.mod",
        "Cargo.toml",
        "pom.xml",
    )
    return _exists_any(root, backend_files)


def detect_target_layout(project_root: Path) -> TargetDetectionResult:
    """Inspect common files and directories without executing project code."""

    root = project_root.resolve()
    comments: list[str] = []
    frontend_paths: list[str] = []
    backend_paths: list[str] = []

    if _exists_any(root, _FRONTEND_SIGNALS) or (root / "src").is_dir():
        frontend_paths.append(".")
    frontend_paths.extend(_existing_dirs(root, _FRONTEND_DIRS))
    backend_paths.extend(_existing_dirs(root, _BACKEND_DIRS))
    if _backend_file_signals(root):
        if "." not in backend_paths:
            backend_paths.append(".")

    monorepo = _exists_any(root, _MONOREPO_SIGNALS) or bool(
        _existing_dirs(root, _MONOREPO_DIRS)
    )
    if monorepo:
        comments.append(
            "monorepo markers detected; review target.surfaces paths manually"
        )

    frontend_paths = tuple(dict.fromkeys(frontend_paths))
    backend_paths = tuple(dict.fromkeys(backend_paths))

    if len(frontend_paths) > 1 or len(backend_paths) > 1:
        return TargetDetectionResult(
            frontend_paths=frontend_paths,
            backend_paths=backend_paths,
            suggested_scope="frontend_only",
            confidence="ambiguous",
            comments=tuple(
                comments
                + [
                    "multiple candidate frontend/backend paths; defaulting to "
                    "frontend_only for safety",
                ]
            ),
        )

    if frontend_paths and not backend_paths:
        return TargetDetectionResult(
            frontend_paths=frontend_paths,
            backend_paths=backend_paths,
            suggested_scope="frontend_only",
            confidence="high",
            comments=tuple(comments),
        )

    if frontend_paths == (".",) and backend_paths and backend_paths != (".",):
        backend = backend_paths[0]
        return TargetDetectionResult(
            frontend_paths=frontend_paths,
            backend_paths=backend_paths,
            suggested_scope="full_stack",
            confidence="medium",
            comments=tuple(
                comments + [f"detected frontend at root and backend at {backend}"]
            ),
        )

    if frontend_paths and backend_paths:
        return TargetDetectionResult(
            frontend_paths=frontend_paths,
            backend_paths=backend_paths,
            suggested_scope="full_stack",
            confidence="medium",
            comments=tuple(comments),
        )

    return TargetDetectionResult(
        frontend_paths=("."),
        backend_paths=(),
        suggested_scope="frontend_only",
        confidence="ambiguous",
        comments=tuple(
            comments
            + ["no strong frontend/backend signals; defaulting to frontend_only"]
        ),
    )


def target_from_detection(
    detection: TargetDetectionResult,
    *,
    frontend_path: str | None = None,
    backend_path: str | None = None,
    backend_reference: bool = False,
    scope_override: str | None = None,
) -> TargetConfig:
    """Build a :class:`TargetConfig` from detection and optional CLI overrides."""

    if scope_override is not None:
        scope = scope_override
        if scope not in VALID_TARGET_SCOPES:
            scope = "frontend_only"
    elif backend_reference and backend_path:
        scope = "frontend_with_backend_reference"
    else:
        scope = detection.suggested_scope

    front = frontend_path or (
        detection.frontend_paths[0] if detection.frontend_paths else "."
    )
    surfaces: dict[str, TargetSurfaceConfig] = {
        "frontend": TargetSurfaceConfig(path=front, editable=True, role="source"),
    }

    back = backend_path or (
        detection.backend_paths[0] if detection.backend_paths else None
    )
    if scope == "full_stack" and back:
        surfaces["backend"] = TargetSurfaceConfig(
            path=back,
            editable=True,
            role="source",
        )
    elif scope == "frontend_with_backend_reference" and back:
        surfaces["backend"] = TargetSurfaceConfig(
            path=back,
            editable=False,
            role="reference",
        )
    elif scope == "full_stack" and not back:
        logger.log(
            1,
            "full_stack requested but no backend path detected; "
            "keeping frontend_only surfaces",
        )
        scope = "frontend_only"

    return TargetConfig(scope=scope, surfaces=surfaces)


def safe_fallback_target() -> TargetConfig:
    """Return the conservative frontend-only target block."""

    return default_target_config()
