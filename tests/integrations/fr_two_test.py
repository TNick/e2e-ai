"""Tests for the e2e-ai fr-two integration adapter (fixtures only)."""

from __future__ import annotations

import textwrap
from pathlib import Path

from e2e_ai.config import load_effective_config
from e2e_ai.integrations.fr_two import (
    build_fr_two_slots,
    build_minio_wipe_request,
    default_fr_two_config,
    fr_two_failure_family,
    is_fr_two_project,
    load_fr_two_manifest,
    map_fr_two_report_to_packet_context,
    render_fr_two_compose_override,
    wipe_fr_two_storage,
    write_fr_two_manifest,
)
from e2e_ai.integrations.fr_two.manifest import FrTwoManifest
from e2e_ai.isolation.models import IsolationContext

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "fr_two"

FR_TWO_YAML = textwrap.dedent(
    """
    project: {id: fr-two}
    state: {dir: .e2e-ai}
    playwright:
      cwd: e2e
      list_command: [pnpm, exec, playwright, test, --list]
      run_command: [pnpm, exec, playwright, test]
    exclude: {tests: ["tests/_diag-.*\\\\.spec\\\\.ts"]}
    target_runtime:
      backend: docker_compose
      cwd: docker
      compose_files: [compose.yml]
      env_files: [env.docker]
      health_checks:
        - {name: backend, kind: http, url: http://127.0.0.1:8000/api/health}
        - {name: frontend, kind: http, url: http://127.0.0.1:8080/}
        - {name: postgres, kind: tcp, host: 127.0.0.1, port: 5432}
    isolation:
      backend: fr_two
      keep_on_failure: true
      keep_on_success: false
      slots:
        count: 4
        database_prefix: frtwo_e2e_slot
        database_user: frtwo
        shared_app_stack: true
        backend_port: 8000
        frontend_port: 8080
        shared_database_name: frtwo
      storage:
        wipe_before_attempt: true
        targets:
          - kind: directory
            name: uploads
            path: "playground/e2e/slots/{slot_id}/uploads"
          - {kind: minio, name: lab-bucket, bucket: frtwo-lab, prefix: "e2e/{slot_id}/"}
    agents:
      planner: {plugin: codex, profile: difficult}
      implementer: {plugin: codex, profile: normal}
    """
)


def _fr_two_config(tmp_path: Path):
    (tmp_path / "e2e").mkdir()
    docker = tmp_path / "docker"
    docker.mkdir()
    (docker / "compose.yml").write_text("services: {}\n", encoding="utf-8")
    (docker / "env.docker").write_text("FOO=bar\n", encoding="utf-8")
    (tmp_path / "e2e-ai.yml").write_text(FR_TWO_YAML, encoding="utf-8")
    return load_effective_config(tmp_path)


def _context(tmp_path: Path) -> IsolationContext:
    config = _fr_two_config(tmp_path)
    return IsolationContext(
        project_root=config.project_root,
        state_dir=config.state_dir,
        config=config,
        env={},
    )


def _slots(project_root: Path):
    return build_fr_two_slots(default_fr_two_config()["isolation"], project_root)


class TestFrTwoDetect:
    def test_detects_fr_two_root(self, tmp_path):
        (tmp_path / "e2e-ai.yml").write_text(
            "project: {id: fr-two}\n", encoding="utf-8"
        )
        assert is_fr_two_project(tmp_path) is True
        other = tmp_path / "other"
        other.mkdir()
        assert is_fr_two_project(other) is False


class TestFrTwoConfig:
    def test_default_config_documents_required_options(self):
        cfg = default_fr_two_config()
        assert cfg["project"]["id"] == "fr-two"
        assert cfg["isolation"]["backend"] == "fr_two"
        assert cfg["isolation"]["slots"]["database_user"]
        assert cfg["isolation"]["storage"]["targets"]
        assert cfg["playwright"]["run_command"]
        assert cfg["full_verification"]["command"] == ["e2e-ai", "verify"]
        assert cfg["target_runtime"]["backend"] == "docker_compose"
        assert cfg["target_runtime"]["health_checks"]
        assert {"planner", "implementer", "instrumenter"} <= set(cfg["agents"])


class TestFrTwoSlot:
    def test_slot_database_name_is_stable(self, tmp_path):
        first = _slots(tmp_path)
        second = _slots(tmp_path)
        assert [s.database_name for s in first] == [s.database_name for s in second]
        assert first[0].database_name == "frtwo"

    def test_slot_database_user_is_stable(self, tmp_path):
        slots = _slots(tmp_path)
        assert {s.database_user for s in slots} == {"frtwo"}

    def test_shared_app_stack_uses_primary_urls(self, tmp_path):
        slots = _slots(tmp_path)
        assert {s.frontend_port for s in slots} == {8080}
        assert {s.backend_port for s in slots} == {8000}

    def test_database_url_includes_password(self, tmp_path):
        slot = _slots(tmp_path)[0]
        assert slot.database_url() == ("postgresql://frtwo:frtwo@127.0.0.1:5432/frtwo")


class TestFrTwoStorage:
    def test_wipes_configured_directories(self, tmp_path):
        context = _context(tmp_path)
        slot = _slots(context.project_root)[0]
        target_dir = (
            context.project_root / "playground" / "e2e" / "slots" / slot.id / "uploads"
        )
        target_dir.mkdir(parents=True)
        (target_dir / "stale.txt").write_text("old", encoding="utf-8")

        targets = [
            {
                "kind": "directory",
                "name": "uploads",
                "path": "playground/e2e/slots/{slot_id}/uploads",
            }
        ]
        wipe_fr_two_storage(context, slot, targets)
        assert not (target_dir / "stale.txt").exists()
        assert target_dir.is_dir()  # recreated clean

    def test_builds_minio_wipe_request(self):
        request = build_minio_wipe_request("frtwo-lab", "e2e/slot0/")
        assert request["bucket"] == "frtwo-lab"
        assert request["prefix"] == "e2e/slot0/"
        assert request["argv"][:2] == ["mc", "rm"]
        assert "e2e/frtwo-lab/e2e/slot0/" in request["argv"][-1]


class TestFrTwoCompose:
    def test_renders_backend_frontend_slots(self, tmp_path):
        context = _context(tmp_path)
        slots = _slots(context.project_root)
        override = render_fr_two_compose_override(context, slots)
        services = override["services"]
        for slot in slots:
            assert f"frtwo-backend-{slot.id}" in services
            assert f"frtwo-frontend-{slot.id}" in services
        backend0 = services["frtwo-backend-slot0"]
        assert backend0["ports"] == ["8000:8000"]
        assert backend0["environment"]["POSTGRES_DB"] == "frtwo"


class TestFrTwoManifest:
    def test_writes_slot_manifest(self, tmp_path):
        context = _context(tmp_path)
        slots = _slots(context.project_root)
        path = write_fr_two_manifest(context, slots)
        manifest = load_fr_two_manifest(path)
        assert manifest.project_id == "fr-two"
        assert len(manifest.slots) == 4
        assert manifest.slot("slot0")["database_name"] == "frtwo"


class TestFrTwoReports:
    def test_maps_report_to_failure_packet_context(self, tmp_path):
        slots = _slots(tmp_path)
        manifest = FrTwoManifest(
            project_id="fr-two",
            created_at="now",
            slots=tuple(
                {
                    "id": s.id,
                    "database_name": s.database_name,
                    "database_user": s.database_user,
                }
                for s in slots
            ),
        )
        ctx = map_fr_two_report_to_packet_context(
            FIXTURES / "playwright-results-failed.json", manifest
        )
        assert ctx["suspected_family"] == "map-filter"
        assert "map-filter" in ctx["spec_file"]
        assert ctx["database_name"] == "frtwo"


class TestFrTwoFamilies:
    def test_detects_map_filter_family(self):
        assert (
            fr_two_failure_family(
                "tests/map-filter.spec.ts", "filter panel not visible", ""
            )
            == "map-filter"
        )

    def test_detects_redlining_family(self):
        assert (
            fr_two_failure_family(
                "tests/redlining.spec.ts", "redlining tool failed", ""
            )
            == "redlining"
        )
