"""Tests for configuration loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from e2e_ai.config import (
    AgentConfig,
    CommandSpec,
    EffectiveConfig,
    PlaywrightConfig,
    ProjectConfig,
    RoutingConfig,
    UserConfig,
    load_effective_config,
    load_project_config,
    load_yaml_file,
    merge_config,
    validate_effective_config,
)
from e2e_ai.config.defaults import DEFAULT_USER_CONFIG
from e2e_ai.config.detect import detect_target_layout
from e2e_ai.config.loader import _parse_project_config, _parse_user_config
from e2e_ai.config.models import (
    RolePreferencesConfig,
    TargetConfig,
    TargetSurfaceConfig,
)
from e2e_ai.config.scaffold import (
    build_scaffold_from_detection,
    render_project_config_yaml,
)
from e2e_ai.config.validation import validate_target_config
from e2e_ai.errors import ConfigError

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


class TestConfigLoader:
    """Configuration loading and merge behavior."""

    def test_missing_user_config_uses_defaults(self) -> None:
        user = _parse_user_config({})
        assert user.routing.allow_canary is False
        assert user.routing.long_task_min_remaining_percent == 25
        assert user.agents == ()

        merged = merge_config(
            UserConfig(agents=DEFAULT_USER_CONFIG.agents, routing=user.routing),
            ProjectConfig(),
            project_root=Path("/tmp/project"),
        )
        codex = next(agent for agent in merged.agents if agent.id == "codex")
        assert codex.executable == "codex"
        assert codex.enabled is True

    def test_project_config_overrides_user_defaults(
        self, tmp_path: Path
    ) -> None:
        user = UserConfig(
            agents=(
                AgentConfig(id="codex", enabled=True, executable="user-codex"),
            ),
            routing=RoutingConfig(allow_canary=True),
        )
        project_file = tmp_path / "e2e-ai.yml"
        project_file.write_text(
            (EXAMPLES / "fr-two.e2e-ai.yml").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        project = load_project_config(tmp_path)
        effective = merge_config(
            user,
            project,
            project_root=tmp_path,
            project_config_path=project_file,
        )
        assert effective.project_id == "fr-two"
        assert effective.routing.allow_canary is True
        codex = next(agent for agent in effective.agents if agent.id == "codex")
        assert codex.executable == "user-codex"
        planner = next(
            agent for agent in effective.agents if agent.id == "planner"
        )
        assert planner.plugin == "cursor_auto"
        assert planner.profile == "difficult"
        prefs = effective.routing.role_preferences
        assert prefs.planner[0] == "cursor_auto"

    def test_agent_lists_merge_by_id(self) -> None:
        user = UserConfig(
            agents=(AgentConfig(id="codex", enabled=True, executable="codex"),),
        )
        project = ProjectConfig(
            agents=(
                AgentConfig(id="codex", profile="difficult"),
                AgentConfig(id="planner", plugin="codex", profile="cheap"),
            ),
        )
        effective = merge_config(
            user,
            project,
            project_root=Path("/tmp/project"),
        )
        codex = next(agent for agent in effective.agents if agent.id == "codex")
        assert codex.executable == "codex"
        assert codex.profile == "difficult"
        assert len(effective.agents) == 2


class TestConfigValidation:
    """Configuration validation rules."""

    def test_rejects_shell_string_command(self, tmp_path: Path) -> None:
        project_file = tmp_path / "e2e-ai.yml"
        project_file.write_text(
            """
project:
  id: demo
playwright:
  list_command: pnpm exec playwright test --list
  run_command:
    - pnpm
    - exec
    - playwright
    - test
""",
            encoding="utf-8",
        )
        with pytest.raises(ConfigError, match="shell string"):
            load_effective_config(
                tmp_path,
                user_config_path=tmp_path / "missing-user.yml",
            )

    def test_rejects_duplicate_agent_ids(self) -> None:
        playwright = PlaywrightConfig(
            list_command=CommandSpec(
                argv=("pnpm", "exec", "playwright", "test", "--list")
            ),
            run_command=CommandSpec(
                argv=("pnpm", "exec", "playwright", "test")
            ),
        )
        config = EffectiveConfig(
            project_id="demo",
            project_root=Path("/tmp"),
            state_dir=Path("/tmp/.e2e-ai"),
            playwright=playwright,
            agents=(
                AgentConfig(id="codex", plugin="codex"),
                AgentConfig(id="codex", plugin="claude"),
            ),
            isolation=ProjectConfig().isolation,
            exclude=(),
            repair_policy=ProjectConfig().repair_policy,
            routing=RoutingConfig(),
        )
        with pytest.raises(ConfigError, match="duplicate agent id"):
            validate_effective_config(config)

    def test_accepts_basic_fr_two_config(self, tmp_path: Path) -> None:
        project_file = tmp_path / "e2e-ai.yml"
        project_file.write_text(
            (EXAMPLES / "fr-two.e2e-ai.yml").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        user_file = tmp_path / "user-config.yml"
        user_file.write_text(
            (EXAMPLES / "user-config.yml").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        config = load_effective_config(tmp_path, user_config_path=user_file)
        assert config.project_id == "fr-two"
        assert config.playwright.list_command is not None
        assert config.playwright.run_command is not None
        assert config.exclude == (r"tests/_diag-.*\.spec\.ts",)
        assert config.project_config_path == project_file.resolve()

    def test_load_yaml_file_returns_mapping(self, tmp_path: Path) -> None:
        path = tmp_path / "sample.yml"
        path.write_text("project:\n  id: x\n", encoding="utf-8")
        data = load_yaml_file(path)
        assert data["project"] == {"id": "x"}

    def test_parse_project_config_reads_example(self) -> None:
        data = load_yaml_file(EXAMPLES / "fr-two.e2e-ai.yml")
        project = _parse_project_config(data)
        assert project.project_id == "fr-two"
        assert project.playwright.cwd == "e2e"
        assert project.target.scope == "full_stack"
        assert project.target.surfaces["backend"].path == "backend"
        assert project.routing is not None
        assert project.routing.role_preferences.planner[0] == "cursor_auto"

    def test_project_routing_overrides_user_role_preferences(self) -> None:
        user = UserConfig(
            agents=DEFAULT_USER_CONFIG.agents,
            routing=RoutingConfig(
                role_preferences=RolePreferencesConfig(
                    planner=("codex", "claude"),
                    implementer=("claude", "codex", "cursor"),
                ),
            ),
        )
        project = ProjectConfig(
            routing=RoutingConfig(
                role_preferences=RolePreferencesConfig(
                    planner=("cursor_auto", "cursor_gpt"),
                ),
            ),
        )
        effective = merge_config(user, project, project_root=Path("/tmp/demo"))
        assert effective.routing.role_preferences.planner == (
            "cursor_auto",
            "cursor_gpt",
        )
        assert effective.routing.role_preferences.implementer == (
            "claude",
            "codex",
            "cursor",
        )

    def test_parse_agent_variant_fields(self) -> None:
        project = _parse_project_config(
            {
                "project": {"id": "demo"},
                "agents": {
                    "cursor_gpt": {
                        "provider": "cursor",
                        "model_candidates": ["gpt-5.6-sol"],
                    },
                },
            }
        )
        variant = next(
            agent for agent in project.agents if agent.id == "cursor_gpt"
        )
        assert variant.provider == "cursor"
        assert variant.model_candidates == ("gpt-5.6-sol",)

    def test_parse_agent_max_turns(self) -> None:
        project = _parse_project_config(
            {
                "project": {"id": "demo"},
                "agents": {
                    "claude": {
                        "provider": "claude",
                        "max_turns": 15,
                    },
                },
            }
        )
        claude = next(agent for agent in project.agents if agent.id == "claude")
        assert claude.max_turns == 15

    def test_merge_agent_max_turns(self) -> None:
        user = UserConfig(
            agents=(AgentConfig(id="claude", provider="claude", max_turns=10),),
            routing=RoutingConfig(),
        )
        project = ProjectConfig(
            agents=(AgentConfig(id="claude", provider="claude", max_turns=15),),
        )
        effective = merge_config(user, project, project_root=Path("/tmp/demo"))
        claude = next(
            agent for agent in effective.agents if agent.id == "claude"
        )
        assert claude.max_turns == 15


class TestTargetConfig:
    """Target scope parsing, detection, and validation."""

    def test_missing_target_defaults_to_frontend_only(self) -> None:
        project = _parse_project_config({"project": {"id": "demo"}})
        assert project.target.scope == "frontend_only"
        assert project.target.surfaces["frontend"].editable is True

    def test_parse_target_surfaces(self) -> None:
        project = _parse_project_config(
            {
                "project": {"id": "demo"},
                "target": {
                    "scope": "frontend_with_backend_reference",
                    "surfaces": {
                        "frontend": {"path": "web", "editable": True},
                        "backend": {
                            "path": "api",
                            "editable": False,
                            "role": "reference",
                        },
                    },
                },
            }
        )
        assert project.target.scope == "frontend_with_backend_reference"
        assert project.target.surfaces["frontend"].path == "web"
        assert project.target.surfaces["backend"].editable is False

    def test_rejects_editable_backend_outside_project_root(
        self, tmp_path: Path
    ) -> None:
        config = EffectiveConfig(
            project_id="demo",
            project_root=tmp_path,
            state_dir=tmp_path / ".e2e-ai",
            playwright=PlaywrightConfig(
                list_command=CommandSpec(argv=("echo", "list")),
                run_command=CommandSpec(argv=("echo", "run")),
            ),
            agents=(),
            isolation=ProjectConfig().isolation,
            exclude=(),
            repair_policy=ProjectConfig().repair_policy,
            routing=RoutingConfig(),
            target=TargetConfig(
                scope="frontend_only",
                surfaces={
                    "frontend": TargetSurfaceConfig(
                        path="../outside",
                        editable=True,
                    ),
                },
            ),
        )
        with pytest.raises(ConfigError, match="outside project root"):
            validate_target_config(config)

    def test_rejects_full_stack_without_backend(self, tmp_path: Path) -> None:
        config = EffectiveConfig(
            project_id="demo",
            project_root=tmp_path,
            state_dir=tmp_path / ".e2e-ai",
            playwright=PlaywrightConfig(
                list_command=CommandSpec(argv=("echo", "list")),
                run_command=CommandSpec(argv=("echo", "run")),
            ),
            agents=(),
            isolation=ProjectConfig().isolation,
            exclude=(),
            repair_policy=ProjectConfig().repair_policy,
            routing=RoutingConfig(),
            target=TargetConfig(
                scope="full_stack",
                surfaces={
                    "frontend": TargetSurfaceConfig(path=".", editable=True),
                },
            ),
        )
        with pytest.raises(ConfigError, match="editable backend"):
            validate_target_config(config)

    def test_detect_frontend_only_layout(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text("{}", encoding="utf-8")
        detection = detect_target_layout(tmp_path)
        assert detection.suggested_scope == "frontend_only"
        assert detection.frontend_paths == (".",)

    def test_detect_full_stack_layout(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text("{}", encoding="utf-8")
        (tmp_path / "backend").mkdir()
        detection = detect_target_layout(tmp_path)
        assert detection.suggested_scope == "full_stack"
        assert "backend" in detection.backend_paths

    def test_render_scaffold_includes_target(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text("{}", encoding="utf-8")
        detection = detect_target_layout(tmp_path)
        scaffold = build_scaffold_from_detection(detection)
        rendered = render_project_config_yaml(scaffold)
        assert "target:" in rendered
        assert "scope: frontend_only" in rendered


class TestRuntimeRefreshConfig:
    def test_parses_refresh_actions_and_rules(self, tmp_path: Path) -> None:
        (tmp_path / "e2e").mkdir()
        (tmp_path / "e2e-ai.yml").write_text(
            (EXAMPLES / "fr-two.e2e-ai.yml").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        config = load_effective_config(tmp_path)
        refresh = config.target_runtime.docker_compose.refresh
        assert refresh is not None
        assert "frontend" in refresh.actions
        assert refresh.actions["frontend"].compose == (
            ("up", "-d", "--build", "frontend"),
        )
        assert refresh.rules[0].paths == ("frontend/**",)

    def test_rejects_refresh_without_docker_compose(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "e2e").mkdir()
        (tmp_path / "e2e-ai.yml").write_text(
            """
            project: {id: demo}
            state: {dir: .e2e-ai}
            playwright:
              cwd: e2e
              list_command: [echo, list]
              run_command: [echo, run]
            target_runtime:
              backend: none
              refresh:
                actions:
                  frontend:
                    description: noop
                    compose: [[restart, frontend]]
                rules: []
            """,
            encoding="utf-8",
        )
        with pytest.raises(ConfigError, match="docker_compose"):
            load_effective_config(tmp_path)

    def test_rejects_unknown_refresh_rule_action(self, tmp_path: Path) -> None:
        (tmp_path / "e2e").mkdir()
        (tmp_path / "e2e-ai.yml").write_text(
            """
            project: {id: demo}
            state: {dir: .e2e-ai}
            playwright:
              cwd: e2e
              list_command: [echo, list]
              run_command: [echo, run]
            target_runtime:
              backend: docker_compose
              compose_files: [compose.yml]
              refresh:
                actions:
                  frontend:
                    description: rebuild frontend
                    compose: [[up, frontend]]
                rules:
                  - paths: [frontend/**]
                    actions: [missing]
            """,
            encoding="utf-8",
        )
        with pytest.raises(ConfigError, match="missing"):
            load_effective_config(tmp_path)
