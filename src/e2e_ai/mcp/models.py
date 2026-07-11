"""Playwright MCP configuration and session models."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from attrs import define, field

DEFAULT_MCP_TOOLS_ALLOW = (
    "browser_navigate",
    "browser_snapshot",
    "browser_find",
    "browser_click",
    "browser_type",
    "browser_press_key",
    "browser_console_messages",
    "browser_network_requests",
    "browser_take_screenshot",
    "browser_close",
)

DEFAULT_MCP_TOOLS_DENY = (
    "browser_run_code_unsafe",
    "browser_file_upload",
    "browser_pdf_save",
    "browser_generate_playwright_test",
)

MCP_SERVER_NAME = "playwright"


@define
class McpOriginsConfig:
    """Origin allowlist policy for MCP browser tasks."""

    from_environment_lease: bool = field(default=True)
    extra_allow: tuple[str, ...] = field(factory=tuple)


@define
class McpStorageStateConfig:
    """Controlled storage-state bootstrap for MCP sessions."""

    mode: str = field(default="none")
    path: str | None = field(default=None)


@define
class PlaywrightMcpConfig:
    """Configuration for task-scoped Playwright MCP sessions.

    Attributes:
        enabled: Whether Playwright MCP can be used.
        version: Exact ``@playwright/mcp`` package version.
        package: NPM package name.
        transport: MCP transport mode.
        browser: Browser name.
        headless: Whether the browser runs headless.
        isolated: Whether browser state is task-scoped.
        output_mode: MCP output mode.
        output_max_mb: Maximum MCP artifact size.
        console_level: Console message verbosity.
        snapshot_mode: Accessibility snapshot mode.
        image_responses: Image response policy.
        unrestricted_file_access: Whether local file access is allowed.
        test_id_attribute: Attribute used by the app for test ids.
        capabilities: Optional MCP capability names.
        tools_allow: Tool allowlist.
        tools_deny: Tool denylist.
        role_enabled: Role-to-enabled mapping.
        origins: Origin allowlist policy.
        storage_state: Storage-state bootstrap policy.
        keep_artifacts_on_failure: Retain MCP output when invocation fails.
    """

    enabled: bool = field(default=False)
    version: str = field(default="0.0.78")
    package: str = field(default="@playwright/mcp")
    transport: str = field(default="stdio")
    browser: str = field(default="chromium")
    headless: bool = field(default=True)
    isolated: bool = field(default=True)
    output_mode: str = field(default="file")
    output_max_mb: int = field(default=100)
    console_level: str = field(default="warning")
    snapshot_mode: str = field(default="full")
    image_responses: str = field(default="omit")
    unrestricted_file_access: bool = field(default=False)
    test_id_attribute: str = field(default="data-testid")
    capabilities: tuple[str, ...] = field(default=("core",))
    tools_allow: tuple[str, ...] = field(default=DEFAULT_MCP_TOOLS_ALLOW)
    tools_deny: tuple[str, ...] = field(default=DEFAULT_MCP_TOOLS_DENY)
    role_enabled: Mapping[str, bool] = field(
        factory=lambda: {
            "planner": False,
            "implementer": True,
            "instrumenter": True,
        }
    )
    origins: McpOriginsConfig = field(factory=McpOriginsConfig)
    storage_state: McpStorageStateConfig = field(factory=McpStorageStateConfig)
    keep_artifacts_on_failure: bool = field(default=True)


@define
class McpSessionSpec:
    """Runtime description of one Playwright MCP session.

    Attributes:
        session_id: Agent invocation scoped session id.
        test_id: Durable e2e-ai test id.
        variant_key: Durable runnable variant key.
        attempt_id: Attempt id.
        role: Agent role.
        output_dir: Directory where MCP artifacts are written.
        config_path: Generated MCP config path.
        allowed_origins: Origins the browser may visit.
        storage_state_path: Optional task-scoped storage state file.
    """

    session_id: str = field()
    test_id: str = field()
    variant_key: str = field()
    attempt_id: str = field()
    role: str = field()
    output_dir: Path = field()
    config_path: Path = field()
    allowed_origins: tuple[str, ...] = field(factory=tuple)
    storage_state_path: Path | None = field(default=None)


@define
class AgentMcpAttachment:
    """MCP details attached to one agent request.

    Attributes:
        enabled: Whether MCP is attached.
        server_name: MCP server name visible to the agent.
        session: Runtime MCP session spec.
        client_config_path: Agent-specific MCP client config path.
        prompt_instructions: Prompt text explaining safe MCP usage.
        required: Whether MCP is required for this invocation.
        degraded_reason: Why MCP was skipped when optional.
    """

    enabled: bool = field(default=False)
    server_name: str = field(default=MCP_SERVER_NAME)
    session: McpSessionSpec | None = field(default=None)
    client_config_path: Path | None = field(default=None)
    prompt_instructions: str = field(default="")
    required: bool = field(default=False)
    degraded_reason: str | None = field(default=None)
    mcp_version: str = field(default="")
    tools_allow: tuple[str, ...] = field(factory=tuple)
    tools_deny: tuple[str, ...] = field(factory=tuple)
