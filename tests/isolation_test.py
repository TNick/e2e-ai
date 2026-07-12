"""Tests for isolation backends and helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from e2e_ai.config import (
    CommandSpec,
    EffectiveConfig,
    IsolationConfig,
    PlaywrightConfig,
    RepairPolicy,
    RoutingConfig,
)
from e2e_ai.errors import ConfigError, DockerError
from e2e_ai.inventory.models import DiscoveredTest
from e2e_ai.isolation import (
    build_compose_argv,
    build_test_database_name,
    create_isolation_backend,
    create_no_isolation_backend,
    find_free_port_range,
    safe_database_name,
)
from e2e_ai.isolation.models import IsolationContext


def _effective_config(
    tmp_path: Path, *, backend: str = "none"
) -> EffectiveConfig:
    playwright = PlaywrightConfig(
        list_command=CommandSpec(argv=("echo", "list")),
        run_command=CommandSpec(argv=("echo", "run")),
        base_url_env="PLAYWRIGHT_BASE_URL",
        api_base_env="PLAYWRIGHT_API_BASE",
    )
    return EffectiveConfig(
        project_id="demo-project",
        project_root=tmp_path,
        state_dir=tmp_path / ".e2e-ai",
        playwright=playwright,
        agents=(),
        isolation=IsolationConfig(backend=backend),
        exclude=(),
        repair_policy=RepairPolicy(),
        routing=RoutingConfig(),
    )


class TestDatabaseNames:
    """PostgreSQL database name helpers."""

    def test_rejects_unsafe_database_name(self) -> None:
        with pytest.raises(DockerError, match="unsafe database identifier"):
            safe_database_name("frtwo-e2e-1")

    def test_test_database_name_fits_postgres_limit(self) -> None:
        long_id = "x" * 200
        name = build_test_database_name("demo-project", long_id, "001-abc")
        assert len(name) <= 63
        safe_database_name(name)


class TestPorts:
    """TCP port availability helpers."""

    def test_find_free_port_range_skips_busy_default(self, monkeypatch) -> None:
        busy = {8101, 8201}

        def fake_port_is_free(host: str, port: int) -> bool:
            return port not in busy

        monkeypatch.setattr(
            "e2e_ai.isolation.ports.port_is_free",
            fake_port_is_free,
        )
        base = find_free_port_range(
            host="127.0.0.1", base=8101, count=2, step=10
        )
        assert base != 8101
        assert fake_port_is_free("127.0.0.1", base)
        assert fake_port_is_free("127.0.0.1", base + 1)


class TestNoIsolation:
    """No-op isolation backend."""

    def test_returns_configured_env(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("PLAYWRIGHT_BASE_URL", "http://localhost:3000")
        monkeypatch.setenv("PLAYWRIGHT_API_BASE", "http://localhost:8000")
        config = _effective_config(tmp_path)
        backend = create_no_isolation_backend(config)
        context = IsolationContext(
            project_root=tmp_path,
            state_dir=tmp_path / ".e2e-ai",
            config=config,
            env={"CUSTOM_FLAG": "1"},
        )
        test = DiscoveredTest(
            id="demo_test",
            title="does a thing",
            spec_file="a.spec.ts",
        )
        lease = backend.create_environment(context, test, "001-abc")
        assert lease.env["CUSTOM_FLAG"] == "1"
        assert lease.env["PLAYWRIGHT_BASE_URL"] == "http://localhost:3000"
        assert lease.env["PLAYWRIGHT_API_BASE"] == "http://localhost:8000"
        assert lease.frontend_url == "http://localhost:3000"
        assert lease.backend_url == "http://localhost:8000"


class TestCompose:
    """Docker Compose argv builders."""

    def test_build_compose_argv_uses_list_arguments(
        self, tmp_path: Path
    ) -> None:
        compose_a = tmp_path / "compose.a.yml"
        compose_b = tmp_path / "compose.b.yml"
        compose_a.write_text("services: {}\n", encoding="utf-8")
        compose_b.write_text("services: {}\n", encoding="utf-8")
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=bar\n", encoding="utf-8")
        argv = build_compose_argv(
            [compose_a, compose_b],
            "demo_project",
            env_file,
            "up",
            "-d",
        )
        assert isinstance(argv, list)
        assert argv[0:3] == ["docker", "compose", "-p"]
        assert argv[3] == "demo_project"
        assert argv[4:6] == ["--env-file", str(env_file)]
        fi = argv.index("-f")
        assert argv[fi + 1] == str(compose_a)
        assert argv[fi + 3] == str(compose_b)
        assert argv[-2:] == ["up", "-d"]


class TestRegistry:
    """Isolation backend factory."""

    def test_unknown_backend_fails_clearly(self, tmp_path: Path) -> None:
        config = _effective_config(tmp_path, backend="not-a-backend")
        with pytest.raises(ConfigError, match="unknown isolation backend"):
            create_isolation_backend(config)

    def test_fr_two_backend_is_available(self, tmp_path: Path) -> None:
        config = _effective_config(tmp_path, backend="fr_two")
        backend = create_isolation_backend(config)
        assert backend.__class__.__name__ == "FrTwoIsolationBackend"
