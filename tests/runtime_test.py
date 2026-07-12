"""Tests for target runtime startup and health checks."""

from __future__ import annotations

import json
import socket
import textwrap
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from e2e_ai.config import load_effective_config
from e2e_ai.config.models import RuntimeHealthCheckConfig
from e2e_ai.errors import TargetRuntimeError
from e2e_ai.runtime.docker_compose import (
    DockerComposeRuntime,
    build_runtime_compose_argv,
    create_docker_compose_runtime,
)
from e2e_ai.runtime.health import (
    wait_for_http_health,
    wait_for_tcp_health,
)
from e2e_ai.runtime.models import RuntimeContext
from e2e_ai.runtime.registry import create_target_runtime
from e2e_ai.runtime.session import build_runtime_context, managed_target_runtime
from e2e_ai.runtime.store import runtime_work_dir, write_command_manifest

RUNTIME_YAML = textwrap.dedent(
    """
    project: {id: demo}
    state: {dir: .e2e-ai}
    playwright:
      cwd: e2e
      list_command: [echo, list]
      run_command: [echo, run]
    exclude: {tests: []}
    target_runtime:
      backend: docker_compose
      cwd: docker
      project_name: demo-runtime
      compose_files: [compose.yml]
      env_files: [env.docker]
      profiles: [e2e]
      services: [postgres, backend]
      env:
        COMPOSE_PROFILES: e2e
      start:
        detach: true
        build: true
        remove_orphans: true
        wait: true
        timeout_seconds: 120
      health_checks:
        - name: backend
          kind: http
          url: http://127.0.0.1:8000/api/health
          timeout_seconds: 2
        - name: postgres
          kind: tcp
          host: 127.0.0.1
          port: 5432
          timeout_seconds: 2
      stop:
        policy: on_success
    agents:
      planner: {plugin: codex}
    """
)


def _runtime_config(tmp_path: Path):
    docker = tmp_path / "docker"
    docker.mkdir()
    (docker / "compose.yml").write_text("services: {}\n", encoding="utf-8")
    (docker / "env.docker").write_text("FOO=bar\n", encoding="utf-8")
    (tmp_path / "e2e").mkdir()
    (tmp_path / "e2e-ai.yml").write_text(RUNTIME_YAML, encoding="utf-8")
    return load_effective_config(tmp_path)


def _context(tmp_path: Path, config, run_id: str = "run-001") -> RuntimeContext:
    return build_runtime_context(config, run_id)


class TestRuntimeConfig:
    def test_loads_docker_compose_runtime(self, tmp_path: Path) -> None:
        config = _runtime_config(tmp_path)
        runtime = config.target_runtime
        assert runtime.backend == "docker_compose"
        assert runtime.docker_compose is not None
        assert runtime.docker_compose.compose_files == ("compose.yml",)
        assert runtime.docker_compose.profiles == ("e2e",)
        assert len(runtime.docker_compose.health_checks) == 2


class TestComposeArgv:
    def test_builds_compose_command_with_files_profiles_services(
        self, tmp_path: Path
    ):
        config = _runtime_config(tmp_path)
        compose = config.target_runtime.docker_compose
        assert compose is not None
        argv = build_runtime_compose_argv(
            compose,
            config.project_root,
            config.project_id,
            "up",
            "-d",
            *compose.services,
        )
        assert argv[:4] == ["docker", "compose", "-p", "demo-runtime"]
        assert "--env-file" in argv
        assert str(tmp_path / "docker" / "env.docker") in argv
        assert "-f" in argv
        assert str(tmp_path / "docker" / "compose.yml") in argv
        assert "--profile" in argv and "e2e" in argv
        assert "postgres" in argv and "backend" in argv


class TestHealthChecks:
    def test_http_health_succeeds(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self.send_response(200)
                self.end_headers()

            def log_message(self, *_args) -> None:
                return

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        try:
            wait_for_http_health(
                RuntimeHealthCheckConfig(
                    name="probe",
                    kind="http",
                    url=f"http://127.0.0.1:{port}/",
                    timeout_seconds=5,
                ),
                {},
            )
        finally:
            server.shutdown()

    def test_tcp_health_times_out(self) -> None:
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        busy_port = sock.getsockname()[1]
        check = RuntimeHealthCheckConfig(
            name="busy",
            kind="tcp",
            host="127.0.0.1",
            port=busy_port,
            timeout_seconds=1,
        )
        with pytest.raises(TargetRuntimeError, match="timed out"):
            wait_for_tcp_health(check)
        sock.close()


class TestDockerComposeRuntime:
    def test_startup_logs_command_manifest(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        config = _runtime_config(tmp_path)
        runtime = create_docker_compose_runtime(config)
        context = _context(tmp_path, config)
        calls: list[list[str]] = []

        def fake_run_compose(argv, *, cwd, env, log_path=None):
            calls.append(list(argv))
            if "up" in argv:
                manifest = runtime_work_dir(config.state_dir, context.run_id)
                write_command_manifest(manifest, argv, label="test")
                return 0
            return 0

        monkeypatch.setattr(
            "e2e_ai.runtime.docker_compose.run_compose",
            fake_run_compose,
        )
        monkeypatch.setattr(
            "e2e_ai.runtime.docker_compose._health_checks_pass",
            lambda *_args, **_kwargs: False,
        )
        monkeypatch.setattr(
            "e2e_ai.runtime.docker_compose.run_health_checks",
            lambda *_args, **_kwargs: None,
        )
        state = runtime.start(context)
        runtime.wait_until_ready(context, state)
        manifest = state.work_dir / "command-manifest.json"
        assert manifest.is_file()
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        assert payload[0]["label"] == "compose-up"
        assert calls[0][0:3] == ["docker", "compose", "-p"]

    def test_stop_policy_on_success(self, tmp_path: Path, monkeypatch) -> None:
        config = _runtime_config(tmp_path)
        runtime = create_docker_compose_runtime(config)
        context = _context(tmp_path, config)
        stopped: list[list[str]] = []

        def fake_run_compose(argv, *, cwd, env, log_path=None):
            if "down" in argv:
                stopped.append(list(argv))
            return 0

        monkeypatch.setattr(
            "e2e_ai.runtime.docker_compose.run_compose",
            fake_run_compose,
        )
        monkeypatch.setattr(
            "e2e_ai.runtime.docker_compose._health_checks_pass",
            lambda *_args, **_kwargs: True,
        )
        state = runtime.start(context)
        runtime.stop(context, state, "passed")
        assert stopped
        runtime.stop(context, state, "failed")
        assert len(stopped) == 1

    def test_stop_policy_never(self, tmp_path: Path, monkeypatch) -> None:
        config = _runtime_config(tmp_path)
        compose = config.target_runtime.docker_compose
        assert compose is not None
        from attrs import evolve

        config = evolve(
            config,
            target_runtime=evolve(
                config.target_runtime,
                docker_compose=evolve(
                    compose,
                    stop=evolve(compose.stop, policy="never"),
                ),
            ),
        )
        runtime = DockerComposeRuntime(
            config=config,
            compose=config.target_runtime.docker_compose,
        )
        context = _context(tmp_path, config)
        calls = {"count": 0}

        def fake_run_compose(argv, *, cwd, env, log_path=None):
            calls["count"] += 1
            return 0

        monkeypatch.setattr(
            "e2e_ai.runtime.docker_compose.run_compose",
            fake_run_compose,
        )
        monkeypatch.setattr(
            "e2e_ai.runtime.docker_compose._health_checks_pass",
            lambda *_args, **_kwargs: True,
        )
        state = runtime.start(context)
        runtime.stop(context, state, "passed")
        assert calls["count"] == 0


class TestRegistry:
    def test_none_backend(self, tmp_path: Path) -> None:
        (tmp_path / "e2e").mkdir()
        (tmp_path / "e2e-ai.yml").write_text(
            textwrap.dedent(
                """
                project: {id: demo}
                state: {dir: .e2e-ai}
                playwright:
                  cwd: e2e
                  list_command: [echo, list]
                  run_command: [echo, run]
                agents:
                  planner: {plugin: codex}
                """
            ),
            encoding="utf-8",
        )
        config = load_effective_config(tmp_path)
        runtime = create_target_runtime(config)
        assert runtime.__class__.__name__ == "NoTargetRuntime"


class TestManagedSession:
    def test_skips_when_backend_none(self, tmp_path: Path) -> None:
        (tmp_path / "e2e").mkdir()
        (tmp_path / "e2e-ai.yml").write_text(
            textwrap.dedent(
                """
                project: {id: demo}
                state: {dir: .e2e-ai}
                playwright:
                  cwd: e2e
                  list_command: [echo, list]
                  run_command: [echo, run]
                agents:
                  planner: {plugin: codex}
                """
            ),
            encoding="utf-8",
        )
        config = load_effective_config(tmp_path)
        order: list[str] = []

        with managed_target_runtime(config, "run-1", enabled=True) as state:
            order.append("inside")
            assert state is None
        assert order == ["inside"]

    def test_runtime_failure_stops_before_yield(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        config = _runtime_config(tmp_path)

        class FailingRuntime:
            def start(self, context):
                raise TargetRuntimeError("startup failed")

            def wait_until_ready(self, context, state):
                return

            def stop(self, context, state, outcome):
                return

        monkeypatch.setattr(
            "e2e_ai.runtime.session.create_target_runtime",
            lambda _config: FailingRuntime(),
        )
        with pytest.raises(TargetRuntimeError, match="startup failed"):
            with managed_target_runtime(config, "run-2"):
                pytest.fail("should not enter body")
