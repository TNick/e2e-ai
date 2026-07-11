"""Build Playwright command lines for a test or a whole spec."""

from __future__ import annotations

from ..config.models import EffectiveConfig
from ..errors import CatalogError
from ..inventory.models import DiscoveredTest

# Reporters/flags appended to every rerun. JSON is written to a file via the
# configured report env var, so it never pollutes stdout.
_BASE_ARGS = ("--reporter=json", "--retries=0")


def _run_argv(config: EffectiveConfig) -> list[str]:
    run_command = config.playwright.run_command
    if run_command is None:
        raise CatalogError("playwright.run_command is not configured")
    return list(run_command.argv)


def _leaf_title(title: str) -> str:
    """Return the innermost test title (Playwright ``-g`` matches a substring)."""

    return title.split("›")[-1].strip() or title


def build_playwright_test_command(
    config: EffectiveConfig,
    test: DiscoveredTest,
) -> list[str]:
    """Return argv for the narrowest supported Playwright rerun.

    Uses ``<run_command> <spec_file> -g <exact-title>``. The title is passed as a
    single argv item (no shell quoting). ``--project`` isolates one browser
    project when the test declares one.
    """

    argv = [*_run_argv(config), test.spec_file, "-g", _leaf_title(test.title)]
    if test.project_name:
        argv += ["--project", test.project_name]
    argv += list(_BASE_ARGS)
    return argv


def build_spec_command(
    config: EffectiveConfig,
    spec_file: str,
) -> list[str]:
    """Return argv for running a whole spec file."""

    return [*_run_argv(config), spec_file, *_BASE_ARGS]
