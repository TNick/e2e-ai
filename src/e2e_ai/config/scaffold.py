"""Project config scaffolding and YAML rendering."""

from __future__ import annotations

from attrs import define, field

from .detect import TargetDetectionResult, safe_fallback_target, target_from_detection
from .models import TargetConfig, TargetSurfaceConfig


@define
class ProjectScaffold:
    """Data used to render a starter ``e2e-ai.yml``."""

    project_id: str = field(default="my-project")
    state_dir: str = field(default=".e2e-ai")
    target: TargetConfig = field(factory=safe_fallback_target)
    comments: tuple[str, ...] = field(factory=tuple)
    include_scope_comments: bool = field(default=False)


def build_scaffold_from_detection(
    detection: TargetDetectionResult,
    *,
    project_id: str = "my-project",
    frontend_path: str | None = None,
    backend_path: str | None = None,
    backend_reference: bool = False,
    scope_override: str | None = None,
) -> ProjectScaffold:
    """Create scaffold data from layout detection and optional overrides."""

    target = target_from_detection(
        detection,
        frontend_path=frontend_path,
        backend_path=backend_path,
        backend_reference=backend_reference,
        scope_override=scope_override,
    )
    comments = list(detection.comments)
    include_comments = detection.confidence == "ambiguous"
    if include_comments:
        comments.extend(
            [
                "To allow coordinated frontend and backend edits, set:",
                "  target.scope: full_stack",
                "  target.surfaces.backend.editable: true",
                "To keep backend read-only for diagnosis, use:",
                "  target.scope: frontend_with_backend_reference",
                "  target.surfaces.backend.role: reference",
            ]
        )
    return ProjectScaffold(
        project_id=project_id,
        target=target,
        comments=tuple(comments),
        include_scope_comments=include_comments,
    )


def _render_surface_yaml(name: str, surface: TargetSurfaceConfig) -> list[str]:
    lines = [
        f"    {name}:",
        f"      path: {surface.path}",
        "      editable: %s" % ("true" if surface.editable else "false"),
    ]
    if surface.role:
        lines.append(f"      role: {surface.role}")
    return lines


def render_project_config_yaml(scaffold: ProjectScaffold) -> str:
    """Render a starter project config including the target section."""

    lines = ["# e2e-ai project configuration."]
    for comment in scaffold.comments:
        lines.append(f"# {comment}")
    lines.extend(
        [
            "project:",
            f"  id: {scaffold.project_id}",
            "",
            "state:",
            f"  dir: {scaffold.state_dir}",
            "",
            "target:",
            f"  scope: {scaffold.target.scope}",
            "  surfaces:",
        ]
    )
    for name, surface in scaffold.target.surfaces.items():
        lines.extend(_render_surface_yaml(name, surface))
    if scaffold.include_scope_comments:
        lines.extend(
            [
                "# Edit target.scope to full_stack or "
                "frontend_with_backend_reference when needed.",
            ]
        )
    lines.extend(
        [
            "",
            "playwright:",
            "  cwd: e2e",
            "  list_command:",
            "    - pnpm",
            "    - exec",
            "    - playwright",
            "    - test",
            "    - --list",
            "  run_command:",
            "    - pnpm",
            "    - exec",
            "    - playwright",
            "    - test",
            "",
            "exclude:",
            "  tests: []",
            "",
            "isolation:",
            "  backend: none",
            "",
            "agents:",
            "  planner:",
            "    plugin: codex",
            "    profile: difficult",
            "  implementer:",
            "    plugin: codex",
            "    profile: cheap",
            "",
        ]
    )
    return "\n".join(lines)
