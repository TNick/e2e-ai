"""Click CLI for e2e-ai."""

from __future__ import annotations

import importlib.metadata
import logging
import os
import sys
from pathlib import Path

import click

from .agents.registry import AgentRegistry
from .config import (
    EffectiveConfig,
    ensure_user_config,
    load_effective_config,
)
from .config.detect import detect_target_layout
from .config.scaffold import build_scaffold_from_detection, render_project_config_yaml
from .config.target import scope_flag_to_value
from .db import database_path, ensure_database
from .errors import ConfigError, DockerError, E2eAiError
from .inventory.store import discover_inventory, ensure_state_layout
from .isolation import (
    POSTGRES_BACKENDS,
    IsolationContext,
    ensure_template_database,
)
from .loop import FixLoop, TestResult, build_backend, default_reporter

logger = logging.getLogger(__name__)


def _version() -> str:
    """Return the installed package version."""

    return importlib.metadata.version("e2e-ai")


def _load(project_root: Path) -> EffectiveConfig:
    try:
        return load_effective_config(project_root.resolve())
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc


def _project_root_option(func):
    return click.option(
        "--project-root",
        type=click.Path(path_type=Path, exists=True, file_okay=False),
        default=".",
        show_default=True,
        help="Directory to treat as the target project root.",
    )(func)


def build_cli() -> click.Group:
    """Build and return the root Click command group."""

    @click.group()
    @click.version_option(_version(), prog_name="e2e-ai")
    @click.option("-v", "--verbose", count=True, help="Increase logging verbosity.")
    @click.pass_context
    def cli(ctx: click.Context, verbose: int) -> None:
        """AI-driven Playwright e2e repair loop."""

        ctx.ensure_object(dict)
        ctx.obj["verbose"] = verbose
        logging.basicConfig(
            level=logging.WARNING - min(verbose, 2) * 10,
            format="%(levelname)s %(name)s: %(message)s",
        )

    # ── doctor ──────────────────────────────────────────────────────────────
    @cli.command()
    @_project_root_option
    def doctor(project_root: Path) -> None:
        """Show resolved configuration paths and health checks."""

        config = _load(project_root)
        project_config = config.project_config_path
        click.echo(f"project id: {config.project_id}")
        click.echo(
            "project config: "
            f"{project_config if project_config is not None else '(not found)'}"
        )
        click.echo(f"user config: {config.user_config_path}")
        click.echo(f"project root: {config.project_root}")
        click.echo(f"state dir: {config.state_dir}")
        click.echo(f"isolation backend: {config.isolation.backend}")
        click.echo(f"exclude patterns: {len(config.exclude)}")
        target = config.target
        click.echo(f"target scope: {target.scope}")
        for name, surface in target.surfaces.items():
            edit = "editable" if surface.editable else "read-only"
            click.echo(f"  surface {name}: {surface.path} ({edit})")
        mcp = config.playwright_mcp
        status = "enabled" if mcp.enabled else "disabled"
        click.echo(f"playwright MCP: {status} (version {mcp.version})")
        if mcp.enabled:
            from .mcp.health import smoke_test_playwright_mcp

            health = smoke_test_playwright_mcp(mcp, config.state_dir / "work")
            mark = "ok" if health.logged_in else "FAIL"
            click.echo(f"[{mark}] playwright MCP: {health.reason}")

    # ── init ────────────────────────────────────────────────────────────────
    @cli.command()
    @click.option("--force", is_flag=True, help="Overwrite an existing project config.")
    @click.option(
        "--target-scope",
        type=click.Choice(
            [
                "frontend-only",
                "full-stack",
                "frontend-with-backend-reference",
            ]
        ),
        default=None,
        help="Declared edit scope for repair agents.",
    )
    @click.option(
        "--frontend-path",
        default=None,
        help="Frontend surface path relative to project root.",
    )
    @click.option(
        "--backend-path",
        default=None,
        help="Backend surface path relative to project root.",
    )
    @click.option(
        "--backend-reference",
        is_flag=True,
        help="Keep backend read-only (frontend_with_backend_reference).",
    )
    def init(
        force: bool,
        target_scope: str | None,
        frontend_path: str | None,
        backend_path: str | None,
        backend_reference: bool,
    ) -> None:
        """Create a starter project config in the current directory."""

        project_root = Path.cwd()
        target = project_root / "e2e-ai.yml"
        if target.exists() and not force:
            raise click.ClickException(
                f"{target} already exists; pass --force to overwrite"
            )

        detection = detect_target_layout(project_root)
        scope_override: str | None = None
        if target_scope is not None:
            scope_override = scope_flag_to_value(target_scope)
        elif backend_reference:
            scope_override = "frontend_with_backend_reference"
        elif detection.confidence == "ambiguous":
            scope_override = click.prompt(
                "Target scope",
                type=click.Choice(
                    [
                        "frontend_only",
                        "full_stack",
                        "frontend_with_backend_reference",
                    ]
                ),
                default=detection.suggested_scope,
            )

        scaffold = build_scaffold_from_detection(
            detection,
            frontend_path=frontend_path,
            backend_path=backend_path,
            backend_reference=backend_reference,
            scope_override=scope_override,
        )
        target.write_text(
            render_project_config_yaml(scaffold),
            encoding="utf-8",
        )
        click.echo(f"Wrote {target}")
        if detection.comments:
            for comment in detection.comments:
                click.echo(f"  note: {comment}")

    # ── discover ────────────────────────────────────────────────────────────
    @cli.command()
    @_project_root_option
    def discover(project_root: Path) -> None:
        """Build the test inventory from ``playwright test --list``."""

        config = _load(project_root)
        counts = discover_inventory(config)
        click.echo(
            f"Discovered {counts.discovered} test(s): "
            f"{counts.runnable} runnable, {counts.excluded} excluded, "
            f"{counts.stale} stale."
        )
        click.echo(f"State database: {database_path(config)}")

    # ── run (single execution pass, no agents) ──────────────────────────────
    @cli.command()
    @_project_root_option
    @click.option("--test-id", default=None, help="Run only the test with this id.")
    @click.option("--all", "run_all", is_flag=True, help="Run all runnable tests.")
    @click.option(
        "--fail-fast",
        is_flag=True,
        help="Stop at the first failing test (default: continue).",
    )
    @click.option("--limit", type=int, default=None, help="Only run the first N tests.")
    @click.option(
        "--rediscover/--no-rediscover",
        default=True,
        show_default=True,
        help="Refresh the inventory before running.",
    )
    def run(
        project_root: Path,
        test_id: str | None,
        run_all: bool,
        fail_fast: bool,
        limit: int | None,
        rediscover: bool,
    ) -> None:
        """Run runnable tests once each and record results (no fixing)."""

        if not run_all and not test_id:
            raise click.ClickException("pass --all or --test-id <id>")

        config = _load(project_root)
        if rediscover:
            discover_inventory(config)
        ensure_state_layout(config)
        backend = _prepare_backend(config)
        conn = ensure_database(database_path(config))
        try:
            loop = FixLoop(
                config,
                conn,
                AgentRegistry.from_config(config),
                backend=backend,
                reporter=default_reporter,
                dry_run=True,
            )
            loop.ensure_dirs()
            # dry_run makes the loop execute each test once and stop before agents.
            summary = loop.run(
                limit=limit,
                test_ids=[test_id] if test_id else None,
                stop_on_failure=fail_fast,
            )
        finally:
            conn.close()
        if test_id and not summary.reports:
            raise click.ClickException(f"no runnable test with id {test_id!r}")
        _print_summary(summary)
        raise SystemExit(0 if summary.all_green else 1)

    # ── repair (the full fix loop) ──────────────────────────────────────────
    @cli.command()
    @_project_root_option
    @click.option(
        "--limit", type=int, default=None, help="Only repair the first N tests."
    )
    @click.option("--test-id", default=None, help="Repair only this test id.")
    @click.option(
        "--max-attempts",
        type=int,
        default=None,
        help="Override repair_policy.max_attempts_per_test.",
    )
    @click.option(
        "--rediscover/--no-rediscover",
        default=True,
        show_default=True,
        help="Refresh the inventory before repairing.",
    )
    @click.option(
        "--skip-login-check",
        is_flag=True,
        help="Do not verify agent logins before starting.",
    )
    @click.option(
        "--dry-run-agents",
        "dry_run_agents",
        is_flag=True,
        help="Build failure packets and prompts without invoking agent CLIs.",
    )
    @click.option(
        "--dry-run",
        is_flag=True,
        help="Alias for --dry-run-agents.",
    )
    def repair(
        project_root: Path,
        limit: int | None,
        test_id: str | None,
        max_attempts: int | None,
        rediscover: bool,
        skip_login_check: bool,
        dry_run_agents: bool,
        dry_run: bool,
    ) -> None:
        """Run the AI fix loop until tests pass or are judged unsolvable."""

        config = _load(project_root)
        if max_attempts is not None:
            from attrs import evolve

            config = evolve(
                config,
                repair_policy=evolve(
                    config.repair_policy,
                    max_attempts_per_test=max_attempts,
                ),
            )
        registry = AgentRegistry.from_config(config)
        agents_dry = dry_run_agents or dry_run

        if not skip_login_check and not agents_dry:
            statuses = registry.require_logins()
            for status in statuses:
                verified = "" if status.verified else " (unverified)"
                click.echo(f"  login ok: {status.agent_id}{verified}")

        if rediscover:
            discover_inventory(config)
        ensure_state_layout(config)
        backend = _prepare_backend(config)
        conn = ensure_database(database_path(config))
        try:
            loop = FixLoop(
                config,
                conn,
                registry,
                backend=backend,
                reporter=default_reporter,
                dry_run=agents_dry,
            )
            loop.ensure_dirs()
            summary = loop.run(
                limit=limit,
                test_ids=[test_id] if test_id else None,
            )
        finally:
            conn.close()
        if test_id and not summary.reports:
            raise click.ClickException(f"no runnable test with id {test_id!r}")
        _print_summary(summary)
        raise SystemExit(0 if summary.all_green else 1)

    # ── agents ──────────────────────────────────────────────────────────────
    @cli.group()
    def agents() -> None:
        """Inspect configured agent plugins."""

    @agents.command(name="list")
    @_project_root_option
    def agents_list(project_root: Path) -> None:
        """List configured agent plugins and role assignments."""

        config = _load(project_root)
        for agent in config.agents:
            if agent.plugin is not None:
                click.echo(
                    f"role {agent.id} -> {agent.plugin}"
                    + (f" (profile: {agent.profile})" if agent.profile else "")
                )
            else:
                state = "enabled" if agent.enabled else "disabled"
                exe = agent.executable or agent.id
                click.echo(f"plugin {agent.id}: {state} (executable: {exe})")

    @agents.command(name="doctor")
    @_project_root_option
    def agents_doctor(project_root: Path) -> None:
        """Check login/health status for the agents this project uses."""

        config = _load(project_root)
        registry = AgentRegistry.from_config(config)
        statuses = registry.check_logins()
        if not statuses:
            click.echo("No agents are referenced by planner/implementer/instrumenter.")
            return
        any_bad = False
        for status in statuses:
            mark = "ok " if status.logged_in else "FAIL"
            verified = "" if status.verified else " [unverified]"
            any_bad = any_bad or not status.logged_in
            click.echo(f"[{mark}] {status.agent_id}{verified}: {status.reason}")
        if config.playwright_mcp.enabled:
            from .mcp.health import smoke_test_playwright_mcp
            from .mcp.policy import should_attach_playwright_mcp

            mcp_required = any(
                should_attach_playwright_mcp(
                    config=config,
                    role=role,
                    failure_family=None,
                )
                for role in ("planner", "implementer", "instrumenter")
            )
            health = smoke_test_playwright_mcp(
                config.playwright_mcp,
                config.state_dir / "work",
            )
            mark = "ok " if health.logged_in else "FAIL"
            if mcp_required:
                any_bad = any_bad or not health.logged_in
            click.echo(
                "[{}] playwright-mcp{}: {}".format(
                    mark,
                    " (required)" if mcp_required else "",
                    health.reason,
                )
            )
        raise SystemExit(1 if any_bad else 0)

    # ── db (isolation backend) ──────────────────────────────────────────────
    @cli.group()
    def db() -> None:
        """Manage the Docker Postgres isolation backend."""

    @db.command(name="template")
    @_project_root_option
    @click.option("--refresh", is_flag=True, help="Recreate the template if it exists.")
    def db_template(project_root: Path, refresh: bool) -> None:
        """Create (or refresh) the pristine template database."""

        config = _load(project_root)
        if config.isolation.backend not in POSTGRES_BACKENDS:
            raise click.ClickException(
                f"isolation backend {config.isolation.backend!r} has no template DB"
            )
        context = _isolation_context(config)
        try:
            if refresh:
                ensure_template_database(context, force=True)
            else:
                ensure_template_database(context)
        except DockerError as exc:
            raise click.ClickException(str(exc)) from exc
        template_db = config.isolation.postgres.template_db
        click.echo(f"Template database {template_db!r} is ready.")

    return cli


def _isolation_context(config: EffectiveConfig) -> IsolationContext:
    return IsolationContext(
        project_root=config.project_root,
        state_dir=config.state_dir,
        config=config,
        env={**os.environ},
    )


def _prepare_backend(config: EffectiveConfig):
    """Build the isolation backend and ensure its baseline exists."""

    backend = build_backend(config)
    if config.isolation.backend in POSTGRES_BACKENDS:
        try:
            backend.prepare_baseline(_isolation_context(config))
        except DockerError as exc:
            raise click.ClickException(
                f"could not prepare isolation backend: {exc}"
            ) from exc
    return backend


def _print_summary(summary) -> None:  # type: ignore[no-untyped-def]
    click.echo("")
    click.echo(
        f"Summary: {len(summary.passed)} passed, "
        f"{len(summary.failed)} failed, {len(summary.blocked)} blocked."
    )
    for report in summary.reports:
        if report.result is not TestResult.PASSED:
            click.echo(
                f"  {report.result.value}: {report.selector}"
                + (f" — {report.note}" if report.note else "")
            )
    if summary.all_green:
        click.echo("All scheduled tests are green. ✅")


def main(args: list[str] | None = None) -> int:
    """Run the e2e-ai command line interface."""

    # The catalog uses the "›" separator and the loop prints status glyphs;
    # force UTF-8 so a legacy console code page (cp1252) cannot crash output.
    for stream in (getattr(sys, "stdout", None), getattr(sys, "stderr", None)):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    try:
        ensure_user_config()
        return build_cli().main(args=args, standalone_mode=False) or 0
    except click.ClickException as exc:
        exc.show()
        return exc.exit_code
    except click.Abort:
        click.echo("Aborted!", err=True)
        return 1
    except E2eAiError as exc:
        click.echo(f"error: {exc}", err=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
