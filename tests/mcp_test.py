"""Tests for Playwright MCP integration."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from e2e_ai.analysis.context import (
    RepairContext,
)
from e2e_ai.analysis.failure_packet import FailurePacket
from e2e_ai.config import load_effective_config
from e2e_ai.errors import ConfigError
from e2e_ai.inventory.models import DiscoveredTest
from e2e_ai.isolation.models import EnvironmentLease
from e2e_ai.mcp.client_configs import (
    render_claude_mcp_config,
    render_codex_mcp_config,
    render_cursor_mcp_config,
)
from e2e_ai.mcp.models import (
    AgentMcpAttachment,
    McpSessionSpec,
    PlaywrightMcpConfig,
)
from e2e_ai.mcp.playwright import build_playwright_mcp_argv, resolve_npx_command
from e2e_ai.mcp.policy import (
    build_allowed_origins,
    should_attach_playwright_mcp,
    validate_playwright_mcp_policy,
)
from e2e_ai.mcp.sessions import cleanup_mcp_session, create_mcp_session_spec
from e2e_ai.orchestrator.prompts import build_mcp_prompt_section

TEST = DiscoveredTest(
    id="demo_abc123",
    title="does a thing",
    spec_file="a.spec.ts",
    project_name="chromium",
    line=42,
    raw_list_line="[chromium] › a.spec.ts › does a thing",
)

PACKET = FailurePacket(
    id="fp_test",
    test_id=TEST.id,
    attempt_id="att-001",
    signature="sig",
    spec_file=TEST.spec_file,
    test_title=TEST.title,
    error_message="boom",
    stack="at a.spec.ts:42:7",
    stdout_tail="",
    stderr_tail="",
    frontend_url="http://localhost:3000/app",
    backend_url="http://localhost:8000/api",
    database_name="e2e_ai_test",
)


def _config(tmp_path: Path, *, mcp_enabled: bool = True):
    yaml = textwrap.dedent(
        """
        project: {id: demo}
        state: {dir: .e2e-ai}
        playwright:
          cwd: e2e
          list_command: [echo, list]
          run_command: [echo, run]
        exclude: {tests: []}
        agents:
          planner: {plugin: claude}
          implementer: {plugin: codex}
          instrumenter: {plugin: claude}
        playwright_mcp:
          enabled: %s
          version: "0.0.78"
          roles:
            planner: false
            implementer: true
            instrumenter: true
        """
        % ("true" if mcp_enabled else "false")
    )
    (tmp_path / "e2e").mkdir()
    (tmp_path / "e2e-ai.yml").write_text(yaml, encoding="utf-8")
    return load_effective_config(tmp_path)


def _context() -> RepairContext:
    return RepairContext(
        packet=PACKET,
        logical_key="a.spec.ts::does a thing",
        variant_key="chromium::a.spec.ts::does a thing",
        project_name="chromium",
        test_list_selector=TEST.raw_list_line,
    )


def _lease() -> EnvironmentLease:
    return EnvironmentLease(
        id="env-1",
        test_id=TEST.id,
        work_dir=Path("/tmp/work"),
        frontend_url="http://localhost:3000",
        backend_url="http://localhost:8000",
    )


def _session(tmp_path: Path) -> McpSessionSpec:
    root = tmp_path / "mcp" / "agent-1"
    output = root / "output"
    config = root / "config" / "playwright-mcp.json"
    output.mkdir(parents=True, exist_ok=True)
    return McpSessionSpec(
        session_id="agent-1",
        test_id=TEST.id,
        variant_key="chromium::a.spec.ts::does a thing",
        attempt_id="att-001",
        role="instrumenter",
        output_dir=output,
        config_path=config,
        allowed_origins=("http://localhost:3000",),
    )


class TestMcpPolicy:
    def test_rejects_latest_version(self) -> None:
        cfg = PlaywrightMcpConfig(enabled=True, version="latest")
        with pytest.raises(ConfigError):
            validate_playwright_mcp_policy(cfg)

    def test_denies_unsafe_tool(self) -> None:
        cfg = PlaywrightMcpConfig(
            enabled=True,
            tools_allow=("browser_run_code_unsafe",),
        )
        with pytest.raises(ConfigError):
            validate_playwright_mcp_policy(cfg)

    def test_uses_lease_origins(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        origins = build_allowed_origins(config=config, lease=_lease())
        assert "http://localhost:3000" in origins
        assert "http://localhost:8000" in origins


class TestMcpCommand:
    def test_builds_npx_command_with_pinned_version(
        self, tmp_path: Path
    ) -> None:
        config = _config(tmp_path).playwright_mcp
        session = _session(tmp_path)
        argv = build_playwright_mcp_argv(config, session)
        assert argv[0] == resolve_npx_command()
        assert "@playwright/mcp@0.0.78" in argv
        assert "--output-mode" in argv
        assert str(session.output_dir) in argv


class TestMcpSession:
    def test_creates_unique_output_dir(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        context = _context()
        lease = _lease()
        first = create_mcp_session_spec(
            config=config,
            context=context,
            lease=lease,
            role="instrumenter",
            agent_invocation_id="agent-a",
        )
        second = create_mcp_session_spec(
            config=config,
            context=context,
            lease=lease,
            role="instrumenter",
            agent_invocation_id="agent-b",
        )
        assert first.output_dir != second.output_dir
        assert first.config_path.is_file()

    def test_keeps_artifacts_on_failure(self, tmp_path: Path) -> None:
        session = _session(tmp_path)
        artifact = session.output_dir / "shot.png"
        artifact.write_bytes(b"png")
        cleanup_mcp_session(session, keep=True)
        assert artifact.is_file()
        cleanup_mcp_session(session, keep=False)
        assert not session.output_dir.parent.exists()


class TestClientConfigs:
    def test_renders_codex_required_server(self, tmp_path: Path) -> None:
        session = _session(tmp_path)
        attachment = AgentMcpAttachment(
            enabled=True,
            session=session,
            tools_allow=("browser_navigate",),
        )
        argv = ["npx", "-y", "@playwright/mcp@0.0.78"]
        text = render_codex_mcp_config(
            attachment, argv=argv, tools_allow=("browser_navigate",)
        )
        assert "required = true" in text
        assert "browser_navigate" in text

    def test_renders_claude_strict_config(self, tmp_path: Path) -> None:
        session = _session(tmp_path)
        attachment = AgentMcpAttachment(enabled=True, session=session)
        text = render_claude_mcp_config(
            attachment,
            argv=["npx", "-y", "@playwright/mcp@0.0.78"],
            tools_allow=("browser_navigate",),
            tools_deny=("browser_run_code_unsafe",),
        )
        assert '"strict": true' in text
        assert "browser_run_code_unsafe" in text

    def test_renders_cursor_single_server(self, tmp_path: Path) -> None:
        session = _session(tmp_path)
        attachment = AgentMcpAttachment(enabled=True, session=session)
        text = render_cursor_mcp_config(
            attachment,
            argv=["npx", "-y", "@playwright/mcp@0.0.78"],
        )
        assert text.count('"playwright"') >= 1
        assert "mcpServers" in text


class TestPrompt:
    def test_mcp_prompt_uses_variant_key(self, tmp_path: Path) -> None:
        context = _context()
        mcp = AgentMcpAttachment(enabled=True, session=_session(tmp_path))
        text = build_mcp_prompt_section(context=context, mcp=mcp)
        assert context.variant_key in text
        assert context.test_list_selector in text

    def test_mcp_prompt_does_not_use_line_as_identity(
        self, tmp_path: Path
    ) -> None:
        context = _context()
        mcp = AgentMcpAttachment(enabled=True, session=_session(tmp_path))
        text = build_mcp_prompt_section(context=context, mcp=mcp)
        assert "source line/column" in text.lower()
        assert "line 42" not in text.lower()
        assert "at a.spec.ts:42" not in text


class TestOrchestrator:
    def test_attaches_mcp_to_instrumenter(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        assert should_attach_playwright_mcp(
            config=config,
            role="instrumenter",
            failure_family="locator",
        )
        assert not should_attach_playwright_mcp(
            config=config,
            role="planner",
            failure_family=None,
        )

    def test_mcp_failure_does_not_count_as_repair_attempt(self) -> None:
        from e2e_ai.orchestrator.models import (
            STATE_EXTERNAL_BLOCKER,
            RepairDecision,
        )

        decision = RepairDecision(
            action="mcp_blocker",
            next_state=STATE_EXTERNAL_BLOCKER,
            stop_run=True,
            reason="mcp_required:node missing",
        )
        assert decision.action == "mcp_blocker"
        assert decision.stop_run
