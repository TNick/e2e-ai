"""Click CLI for e2e-ai."""

from __future__ import annotations

import importlib.metadata
import json
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
from .errors import ConfigError, DockerError, E2eAiError, TargetRuntimeError
from .inventory.store import discover_inventory, ensure_state_layout
from .isolation import (
    POSTGRES_BACKENDS,
    IsolationContext,
    drop_database,
    ensure_template_database,
)
from .loop import FixLoop, TestResult, build_backend, default_reporter
from .runner.results import load_playwright_json, summarize_playwright_json
from .runtime.docker_compose import resolve_runtime_path, runtime_cwd

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


def _repair_options(func):
    func = click.option(
        "--limit",
        type=int,
        default=None,
        help="Only repair the first N tests.",
    )(func)
    func = click.option("--test-id", default=None, help="Repair only this test id.")(
        func
    )
    func = click.option(
        "--max-attempts",
        type=int,
        default=None,
        help="Override repair_policy.max_attempts_per_test.",
    )(func)
    func = click.option(
        "--rediscover/--no-rediscover",
        default=True,
        show_default=True,
        help="Refresh the inventory before repairing.",
    )(func)
    func = click.option(
        "--skip-login-check",
        is_flag=True,
        help="Do not verify agent logins before starting.",
    )(func)
    func = click.option(
        "--dry-run-agents",
        "dry_run_agents",
        is_flag=True,
        help="Build failure packets and prompts without invoking agent CLIs.",
    )(func)
    func = click.option(
        "--dry-run",
        is_flag=True,
        help="Alias for --dry-run-agents.",
    )(func)
    func = click.option(
        "--failed-only",
        is_flag=True,
        help="Repair only tests that did not pass in the previous finished run.",
    )(func)
    func = click.option(
        "--start-runtime/--no-start-runtime",
        default=True,
        show_default=True,
        help="Start configured target Docker support before running tests.",
    )(func)
    return func


def _run_repair(
    *,
    project_root: Path,
    limit: int | None,
    test_id: str | None,
    max_attempts: int | None,
    rediscover: bool,
    skip_login_check: bool,
    dry_run_agents: bool,
    dry_run: bool,
    start_runtime: bool,
    failed_only: bool,
) -> None:
    """Run the repair loop using the shared CLI options."""

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
    conn = ensure_database(database_path(config), project_id=config.project_id)
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
            only_failed=failed_only,
            start_runtime=start_runtime,
        )
    except TargetRuntimeError as exc:
        raise click.ClickException(str(exc)) from exc
    finally:
        conn.close()
    if failed_only and not summary.reports and summary.all_green:
        click.echo("No runnable tests failed in the previous run.")
        raise SystemExit(0)
    if test_id and not summary.reports:
        raise click.ClickException(f"no runnable test with id {test_id!r}")
    _exit_from_summary(summary)


def _exit_from_summary(summary, *, verify: bool = False) -> None:  # type: ignore[no-untyped-def]
    _print_summary(summary)
    if summary.interrupted:
        raise SystemExit(130)
    if verify:
        if summary.all_green:
            click.echo("VERIFY: clean ✅")
            raise SystemExit(0)
        click.echo("VERIFY: not clean ❌")
        raise SystemExit(1)
    raise SystemExit(0 if summary.all_green else 1)


def build_cli() -> click.Group:
    """Build and return the root Click command group."""

    @click.group(
        invoke_without_command=True,
        help=(
            "AI-driven Playwright e2e repair loop. When no subcommand is "
            "given, `e2e-ai` runs `repair` with the same options."
        ),
    )
    @click.version_option(_version(), prog_name="e2e-ai")
    @_project_root_option
    @_repair_options
    @click.option("-v", "--verbose", count=True, help="Increase logging verbosity.")
    @click.pass_context
    def cli(
        ctx: click.Context,
        verbose: int,
        project_root: Path,
        limit: int | None,
        test_id: str | None,
        max_attempts: int | None,
        rediscover: bool,
        skip_login_check: bool,
        dry_run_agents: bool,
        dry_run: bool,
        start_runtime: bool,
        failed_only: bool,
    ) -> None:
        """AI-driven Playwright e2e repair loop."""

        ctx.ensure_object(dict)
        ctx.obj["verbose"] = verbose
        logging.basicConfig(
            level=logging.WARNING - min(verbose, 2) * 10,
            format="%(levelname)s %(name)s: %(message)s",
        )
        if ctx.invoked_subcommand is None and not ctx.resilient_parsing:
            _run_repair(
                project_root=project_root,
                limit=limit,
                test_id=test_id,
                max_attempts=max_attempts,
                rediscover=rediscover,
                skip_login_check=skip_login_check,
                dry_run_agents=dry_run_agents,
                dry_run=dry_run,
                start_runtime=start_runtime,
                failed_only=failed_only,
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
        runtime = config.target_runtime
        click.echo(f"target runtime: {runtime.backend}")
        if runtime.backend == "docker_compose" and runtime.docker_compose is not None:
            compose = runtime.docker_compose
            click.echo(f"runtime compose files: {len(compose.compose_files)}")
            if compose.services:
                click.echo(f"runtime services: {', '.join(compose.services)}")
            else:
                click.echo("runtime services: (all)")
            click.echo(f"runtime stop policy: {compose.stop.policy}")
            click.echo(f"runtime health checks: {len(compose.health_checks)}")
            cwd = runtime_cwd(config.project_root, compose)
            missing = [
                path
                for path in compose.compose_files
                if not resolve_runtime_path(
                    config.project_root,
                    path,
                    base=cwd,
                ).is_file()
            ]
            if missing:
                click.echo(
                    "runtime compose files missing: "
                    + ", ".join(str(item) for item in missing)
                )
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
    @click.option(
        "--start-runtime/--no-start-runtime",
        default=True,
        show_default=True,
        help="Start configured target Docker support before running tests.",
    )
    @click.option(
        "--shard-min-tests",
        type=int,
        default=None,
        help="Run until each fr-two slot has at least N passing tests.",
    )
    def run(
        project_root: Path,
        test_id: str | None,
        run_all: bool,
        fail_fast: bool,
        limit: int | None,
        rediscover: bool,
        start_runtime: bool,
        shard_min_tests: int | None,
    ) -> None:
        """Run runnable tests once each and record results (no fixing)."""

        if not run_all and not test_id:
            raise click.ClickException("pass --all or --test-id <id>")

        config = _load(project_root)
        if rediscover:
            discover_inventory(config)
        ensure_state_layout(config)
        backend = _prepare_backend(config)
        conn = ensure_database(database_path(config), project_id=config.project_id)
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
                start_runtime=start_runtime,
                min_tests_per_slot=shard_min_tests,
            )
        except TargetRuntimeError as exc:
            raise click.ClickException(str(exc)) from exc
        finally:
            conn.close()
        if test_id and not summary.reports:
            raise click.ClickException(f"no runnable test with id {test_id!r}")
        _exit_from_summary(summary)

    # ── repair (the full fix loop) ──────────────────────────────────────────
    @cli.command()
    @_project_root_option
    @_repair_options
    def repair(
        project_root: Path,
        limit: int | None,
        test_id: str | None,
        max_attempts: int | None,
        rediscover: bool,
        skip_login_check: bool,
        dry_run_agents: bool,
        dry_run: bool,
        start_runtime: bool,
        failed_only: bool,
    ) -> None:
        """Run the AI fix loop until tests pass or are judged unsolvable."""
        _run_repair(
            project_root=project_root,
            limit=limit,
            test_id=test_id,
            max_attempts=max_attempts,
            rediscover=rediscover,
            skip_login_check=skip_login_check,
            dry_run_agents=dry_run_agents,
            dry_run=dry_run,
            start_runtime=start_runtime,
            failed_only=failed_only,
        )

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

    # ── verify (clean gate) ──────────────────────────────────────────────────
    @cli.command()
    @_project_root_option
    @click.option(
        "--report",
        "reports",
        multiple=True,
        type=click.Path(path_type=Path, exists=True),
        help="Gate an existing Playwright JSON report (file or dir). Repeatable. "
        "Accepts sharded runs. When omitted, the full suite is run once.",
    )
    @click.option(
        "--allow-skips",
        is_flag=True,
        help="Do not fail the gate on skipped tests.",
    )
    @click.option(
        "--rediscover/--no-rediscover",
        default=True,
        show_default=True,
        help="Refresh the inventory before running (run mode only).",
    )
    @click.option(
        "--limit", type=int, default=None, help="Only run the first N tests (run mode)."
    )
    @click.option(
        "--start-runtime/--no-start-runtime",
        default=True,
        show_default=True,
        help="Start configured target Docker support before running tests.",
    )
    def verify(
        project_root: Path,
        reports: tuple[Path, ...],
        allow_skips: bool,
        rediscover: bool,
        limit: int | None,
        start_runtime: bool,
    ) -> None:
        """Assert an E2E run is clean — the final gate.

        With ``--report`` it parses existing Playwright JSON reports (including
        sharded runs) and gates on them. Otherwise it runs the full runnable
        suite once (no agents) and gates on the result.
        """

        # Report mode gates existing artifacts and needs no project config.
        if reports:
            clean = _gate_reports(list(reports), allow_skips=allow_skips)
            raise SystemExit(0 if clean else 1)

        config = _load(project_root)
        if rediscover:
            discover_inventory(config)
        ensure_state_layout(config)
        backend = _prepare_backend(config)
        conn = ensure_database(database_path(config), project_id=config.project_id)
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
            summary = loop.run(limit=limit, start_runtime=start_runtime)
        except TargetRuntimeError as exc:
            raise click.ClickException(str(exc)) from exc
        finally:
            conn.close()
        _exit_from_summary(summary, verify=True)

    # ── cleanup ──────────────────────────────────────────────────────────────
    @cli.command()
    @_project_root_option
    @click.option(
        "--dry-run", is_flag=True, help="Show what would be removed without doing it."
    )
    @click.option(
        "--purge-artifacts",
        is_flag=True,
        help="Also delete per-attempt work/run artifacts (destructive).",
    )
    @click.option(
        "--stale-runs",
        is_flag=True,
        help="Mark orphaned running repair runs as stopped when their master PID "
        "is gone.",
    )
    def cleanup(
        project_root: Path,
        dry_run: bool,
        purge_artifacts: bool,
        stale_runs: bool,
    ) -> None:
        """Drop kept isolation databases and (optionally) purge artifacts.

        Databases kept for debugging (``keep_on_failure`` / ``keep_on_success``)
        record a ``cleanup-manifest.json`` under the state dir; this drops each
        recorded database. Artifacts referenced by kept environments are removed
        only with ``--purge-artifacts``.
        """

        config = _load(project_root)
        if stale_runs:
            from .repair.stale_runs import reconcile_stale_runs

            conn = ensure_database(
                database_path(config),
                reconcile_stale_runs=False,
            )
            try:
                result = reconcile_stale_runs(
                    conn,
                    project_id=config.project_id,
                    dry_run=dry_run,
                )
            finally:
                conn.close()
            verb = "Would stop" if dry_run else "Stopped"
            click.echo(f"{verb} {len(result.stopped_run_ids)} stale run(s).")
            for run_id in result.stopped_run_ids:
                click.echo(f"  {run_id}")

        dropped, failed = _cleanup_databases(config, dry_run=dry_run)
        verb = "Would drop" if dry_run else "Dropped"
        click.echo(f"{verb} {dropped} kept database(s).")
        for name, reason in failed:
            click.echo(f"  could not drop {name}: {reason}", err=True)

        if purge_artifacts:
            removed = _purge_artifacts(config, dry_run=dry_run)
            verb = "Would remove" if dry_run else "Removed"
            click.echo(f"{verb} {removed} artifact director(y/ies).")

        raise SystemExit(0)

    # ── ui (local read-only web monitor) ─────────────────────────────────────
    @cli.command()
    @_project_root_option
    @click.option(
        "--host",
        default=None,
        help="Host/interface to bind (default: monitor.host or "
        "127.0.0.1). Non-loopback prints a warning.",
    )
    @click.option(
        "--port",
        type=int,
        default=None,
        help="Port to serve on (default: monitor.port or 8765).",
    )
    @click.option(
        "--refresh-ms",
        type=int,
        default=None,
        help="Live-refresh interval hint in ms (default: monitor.refresh_ms or 1000).",
    )
    @click.option(
        "--db",
        "db_path",
        type=click.Path(path_type=Path),
        default=None,
        help="State database path (default: from config).",
    )
    @click.option(
        "--open",
        "open_browser",
        is_flag=True,
        help="Open the dashboard in a browser after starting.",
    )
    def ui(
        project_root: Path,
        host: str | None,
        port: int | None,
        refresh_ms: int | None,
        db_path: Path | None,
        open_browser: bool,
    ) -> None:
        """Start a local, read-only web monitor for the state database.

        Host, port, and refresh interval default to the ``monitor`` section of
        the project config; the CLI flags override them.
        """

        from .config import MonitorConfig
        from .monitor import (
            MonitorError,
            build_monitor,
            ensure_monitor_extra,
            run_server,
        )

        try:
            ensure_monitor_extra()
        except MonitorError as exc:
            raise click.ClickException(str(exc)) from exc

        config: EffectiveConfig | None
        if db_path is not None:
            resolved_db = db_path.resolve()
            try:
                config = load_effective_config(project_root.resolve())
                project_id = config.project_id
                state_dir = config.state_dir
                proot = config.project_root
            except E2eAiError:
                config = None
                proot = project_root.resolve()
                state_dir = resolved_db.parent
                project_id = ""
        else:
            config = _load(project_root)
            resolved_db = database_path(config)
            project_id = config.project_id
            state_dir = config.state_dir
            proot = config.project_root

        # Resolve host/port/refresh: CLI flag > config monitor section > default.
        monitor_cfg = config.monitor if config is not None else MonitorConfig()
        host = host if host is not None else monitor_cfg.host
        port = port if port is not None else monitor_cfg.port
        refresh_ms = refresh_ms if refresh_ms is not None else monitor_cfg.refresh_ms
        open_browser = open_browser or monitor_cfg.open_browser

        # Full merged config (defaults + user + project) for the Settings page.
        config_full = None
        if config is not None:
            from attrs import asdict

            config_full = json.loads(json.dumps(asdict(config), default=str))

        if host not in ("127.0.0.1", "localhost", "::1"):
            click.echo(
                f"WARNING: binding to {host} exposes the read-only monitor beyond "
                "this machine. A future release will require an access token for "
                "non-loopback hosts.",
                err=True,
            )

        if resolved_db.is_file():
            conn = ensure_database(
                resolved_db,
                project_id=project_id or None,
            )
            conn.close()

        try:
            app, _store, _procs, _info = build_monitor(
                db_path=resolved_db,
                project_root=proot,
                state_dir=state_dir,
                project_id=project_id,
                host=host,
                port=port,
                refresh_ms=refresh_ms,
                config_full=config_full,
            )
        except MonitorError as exc:
            raise click.ClickException(str(exc)) from exc

        url = f"http://{host}:{port}/"
        click.echo(f"e2e-ai monitor on {url}")
        click.echo(f"state database: {resolved_db}")
        if open_browser:
            import webbrowser

            webbrowser.open(url)
        run_server(app, host=host, port=port)

    return cli


def _isolation_context(config: EffectiveConfig) -> IsolationContext:
    return IsolationContext(
        project_root=config.project_root,
        state_dir=config.state_dir,
        config=config,
        env={**os.environ},
    )


def _prepare_backend(config: EffectiveConfig):
    """Build the isolation backend for a command run."""

    return build_backend(config)


def _iter_report_files(paths: list[Path]) -> list[Path]:
    """Expand report paths (files or directories) into JSON report files."""

    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(sorted(path.rglob("*.json")))
        elif path.is_file():
            files.append(path)
    return files


def _gate_reports(paths: list[Path], *, allow_skips: bool) -> bool:
    """Parse Playwright report(s) and return whether the run is clean."""

    files = _iter_report_files(paths)
    if not files:
        raise click.ClickException("no Playwright JSON reports found to verify")

    totals = {"expected": 0, "unexpected": 0, "skipped": 0, "flaky": 0}
    parsed = 0
    for file in files:
        data = load_playwright_json(file)
        if not data or "stats" not in data:
            continue  # not a Playwright report; skip quietly
        parsed += 1
        stats = summarize_playwright_json(data)
        for key in totals:
            totals[key] += int(stats.get(key, 0))

    if parsed == 0:
        raise click.ClickException("none of the given files are Playwright reports")

    click.echo(
        "VERIFY reports: {expected} passed, {unexpected} failed, "
        "{flaky} flaky, {skipped} skipped "
        "(across {n} report(s))".format(n=parsed, **totals)
    )

    clean = (
        totals["unexpected"] == 0
        and totals["flaky"] == 0
        and totals["expected"] >= 1
        and (allow_skips or totals["skipped"] == 0)
    )
    click.echo("VERIFY: clean ✅" if clean else "VERIFY: not clean ❌")
    return clean


def _cleanup_databases(
    config: EffectiveConfig,
    *,
    dry_run: bool,
) -> tuple[int, list[tuple[str, str]]]:
    """Drop databases recorded in cleanup manifests under the state dir."""

    dropped = 0
    failed: list[tuple[str, str]] = []
    context = _isolation_context(config)
    for manifest_path in sorted(config.state_dir.rglob("cleanup-manifest.json")):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            failed.append((str(manifest_path), str(exc)))
            continue
        database = manifest.get("database_name")
        if not database:
            manifest_path.unlink(missing_ok=True)
            continue
        if dry_run:
            click.echo(f"  would drop {database}")
            dropped += 1
            continue
        try:
            drop_database(context, database)
            dropped += 1
            manifest_path.unlink(missing_ok=True)
        except (DockerError, E2eAiError) as exc:
            failed.append((database, str(exc)))
    return dropped, failed


def _purge_artifacts(config: EffectiveConfig, *, dry_run: bool) -> int:
    """Remove per-attempt work/run artifact directories (destructive)."""

    import shutil

    removed = 0
    for name in ("work", "runs"):
        target = config.state_dir / name
        if not target.is_dir():
            continue
        if dry_run:
            click.echo(f"  would remove {target}")
        else:
            shutil.rmtree(target, ignore_errors=True)
        removed += 1
    return removed


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
