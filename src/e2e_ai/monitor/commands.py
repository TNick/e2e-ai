"""Allowlisted e2e-ai commands the monitor may launch, and safe argv building.

Commands are declared as data (never free-form shell text). The UI renders a
form from :func:`command_schema` and posts values back; :func:`build_argv`
validates them and produces an argv list of the form
``[python, -m, e2e_ai, <subcommand>, <validated flags>]``. Nothing here uses a
shell.
"""

from __future__ import annotations

import sys
from typing import Any

from attrs import define, field

from .store import MonitorError

# Field types exposed to the UI form renderer.
FIELD_TYPES = frozenset(
    {"text", "integer", "boolean", "toggle", "choice", "path", "repeatable_path"}
)


class CommandValidationError(MonitorError):
    """Raised when a command id or its submitted options are invalid."""


@define
class CommandOption:
    """One option of a launchable command, mirroring a Click option."""

    name: str = field()
    type: str = field()
    flag: str = field(default="")
    false_flag: str | None = field(default=None)  # toggle off flag (--no-x)
    default: Any = field(default=None)
    choices: tuple[str, ...] = field(factory=tuple)
    help: str = field(default="")

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "flag": self.flag,
            "false_flag": self.false_flag,
            "default": self.default,
            "choices": list(self.choices),
            "help": self.help,
        }


@define
class CommandDef:
    """A launchable e2e-ai command."""

    id: str = field()
    label: str = field()
    argv_prefix: tuple[str, ...] = field()
    options: tuple[CommandOption, ...] = field(factory=tuple)
    long_running: bool = field(default=False)
    destructive: bool = field(default=False)
    concurrent: bool = field(default=True)

    def schema(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "argv_prefix": list(self.argv_prefix),
            "long_running": self.long_running,
            "destructive": self.destructive,
            "concurrent": self.concurrent,
            "options": [opt.schema() for opt in self.options],
        }

    def option(self, name: str) -> CommandOption | None:
        return next((o for o in self.options if o.name == name), None)


def _project_root_option() -> CommandOption:
    return CommandOption(
        name="project_root",
        type="path",
        flag="--project-root",
        default=".",
        help="Directory to treat as the target project root.",
    )


def _rediscover_toggle(help_: str) -> CommandOption:
    return CommandOption(
        name="rediscover",
        type="toggle",
        flag="--rediscover",
        false_flag="--no-rediscover",
        default=True,
        help=help_,
    )


def _start_runtime_toggle() -> CommandOption:
    return CommandOption(
        name="start_runtime",
        type="toggle",
        flag="--start-runtime",
        false_flag="--no-start-runtime",
        default=True,
        help="Start the configured target Docker support before running tests.",
    )


COMMANDS: dict[str, CommandDef] = {
    "doctor": CommandDef(
        id="doctor",
        label="Doctor",
        argv_prefix=("doctor",),
        options=(_project_root_option(),),
    ),
    "discover": CommandDef(
        id="discover",
        label="Discover tests",
        argv_prefix=("discover",),
        options=(_project_root_option(),),
    ),
    "run": CommandDef(
        id="run",
        label="Run (no agents)",
        argv_prefix=("run",),
        long_running=True,
        concurrent=False,
        options=(
            _project_root_option(),
            CommandOption(
                "test_id", "text", "--test-id", help="Run only the test with this id."
            ),
            CommandOption(
                "all", "boolean", "--all", default=False, help="Run all runnable tests."
            ),
            CommandOption(
                "fail_fast",
                "boolean",
                "--fail-fast",
                default=False,
                help="Stop at the first failing test.",
            ),
            CommandOption(
                "limit", "integer", "--limit", help="Only run the first N tests."
            ),
            _rediscover_toggle("Refresh the inventory before running."),
            _start_runtime_toggle(),
            CommandOption(
                "shard_min_tests",
                "integer",
                "--shard-min-tests",
                help="Run until each slot has at least N passing tests.",
            ),
        ),
    ),
    "repair": CommandDef(
        id="repair",
        label="Repair (AI fix loop)",
        argv_prefix=("repair",),
        long_running=True,
        destructive=True,
        concurrent=False,
        options=(
            _project_root_option(),
            CommandOption(
                "limit", "integer", "--limit", help="Only repair the first N tests."
            ),
            CommandOption(
                "test_id", "text", "--test-id", help="Repair only this test id."
            ),
            CommandOption(
                "max_attempts",
                "integer",
                "--max-attempts",
                help="Override repair_policy.max_attempts_per_test.",
            ),
            _rediscover_toggle("Refresh the inventory before repairing."),
            CommandOption(
                "skip_login_check",
                "boolean",
                "--skip-login-check",
                default=False,
                help="Do not verify agent logins.",
            ),
            CommandOption(
                "dry_run_agents",
                "boolean",
                "--dry-run-agents",
                default=False,
                help="Build prompts without invoking agent CLIs.",
            ),
            CommandOption(
                "failed_only",
                "boolean",
                "--failed-only",
                default=False,
                help="Repair only tests that failed in the previous finished run.",
            ),
            _start_runtime_toggle(),
        ),
    ),
    "verify": CommandDef(
        id="verify",
        label="Verify (clean gate)",
        argv_prefix=("verify",),
        long_running=True,
        concurrent=False,
        options=(
            _project_root_option(),
            CommandOption(
                "report",
                "repeatable_path",
                "--report",
                help="Gate existing Playwright JSON reports (file/dir).",
            ),
            CommandOption(
                "allow_skips",
                "boolean",
                "--allow-skips",
                default=False,
                help="Do not fail the gate on skipped tests.",
            ),
            _rediscover_toggle("Refresh the inventory before running (run mode)."),
            CommandOption(
                "limit",
                "integer",
                "--limit",
                help="Only run the first N tests (run mode).",
            ),
            _start_runtime_toggle(),
        ),
    ),
    "cleanup": CommandDef(
        id="cleanup",
        label="Cleanup",
        argv_prefix=("cleanup",),
        destructive=True,
        concurrent=False,
        options=(
            _project_root_option(),
            CommandOption(
                "dry_run",
                "boolean",
                "--dry-run",
                default=False,
                help="Show what would be removed without doing it.",
            ),
            CommandOption(
                "purge_artifacts",
                "boolean",
                "--purge-artifacts",
                default=False,
                help="Also delete per-attempt work/run artifacts.",
            ),
            CommandOption(
                "stale_runs",
                "boolean",
                "--stale-runs",
                default=False,
                help="Mark orphaned running repair runs as stopped.",
            ),
        ),
    ),
    "agents-list": CommandDef(
        id="agents-list",
        label="Agents: list",
        argv_prefix=("agents", "list"),
        options=(_project_root_option(),),
    ),
    "agents-doctor": CommandDef(
        id="agents-doctor",
        label="Agents: doctor",
        argv_prefix=("agents", "doctor"),
        options=(_project_root_option(),),
    ),
    "db-template": CommandDef(
        id="db-template",
        label="DB: build template",
        argv_prefix=("db", "template"),
        destructive=True,
        concurrent=False,
        options=(
            _project_root_option(),
            CommandOption(
                "refresh",
                "boolean",
                "--refresh",
                default=False,
                help="Recreate the template if it exists.",
            ),
        ),
    ),
}


def command_schema() -> list[dict[str, Any]]:
    """Return JSON-serializable metadata for all allowlisted commands."""

    return [cmd.schema() for cmd in COMMANDS.values()]


def get_command(command_id: str) -> CommandDef:
    cmd = COMMANDS.get(command_id)
    if cmd is None:
        raise CommandValidationError(f"unknown command {command_id!r}")
    return cmd


def _coerce_int(name: str, value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise CommandValidationError(f"option {name!r} must be an integer") from None


def build_argv(
    command_id: str,
    values: dict[str, Any] | None = None,
    *,
    python_executable: str | None = None,
) -> list[str]:
    """Validate submitted option values and build a safe argv list.

    Raises :class:`CommandValidationError` for an unknown command or any option
    that is not declared for it.
    """

    cmd = get_command(command_id)
    values = dict(values or {})

    known = {opt.name for opt in cmd.options}
    unknown = set(values) - known
    if unknown:
        raise CommandValidationError(
            f"unknown option(s) for {command_id!r}: {', '.join(sorted(unknown))}"
        )

    python = python_executable or sys.executable
    argv: list[str] = [python, "-m", "e2e_ai", *cmd.argv_prefix]

    for opt in cmd.options:
        provided = opt.name in values
        value = values.get(opt.name, opt.default)

        if opt.type == "boolean":
            if value:
                argv.append(opt.flag)
        elif opt.type == "toggle":
            argv.append(opt.flag if value else (opt.false_flag or opt.flag))
        elif opt.type == "integer":
            if value is None or value == "":
                continue
            argv += [opt.flag, str(_coerce_int(opt.name, value))]
        elif opt.type == "repeatable_path":
            items = value or []
            if isinstance(items, (str, bytes)):
                items = [items]
            for item in items:
                if str(item).strip():
                    argv += [opt.flag, str(item)]
        elif opt.type == "choice":
            if value in (None, ""):
                continue
            if opt.choices and str(value) not in opt.choices:
                raise CommandValidationError(
                    f"option {opt.name!r} must be one of {', '.join(opt.choices)}"
                )
            argv += [opt.flag, str(value)]
        else:  # text, path
            if value in (None, ""):
                continue
            argv += [opt.flag, str(value)]
        _ = provided
    return argv
