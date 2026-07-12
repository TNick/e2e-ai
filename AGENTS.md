# Agent Guidelines

## Changelog

When you change production code in this repository, update the nearest
`CHANGELOG.md` (repository root) under `## [Unreleased]` with user-facing
bullets in the appropriate `Added`, `Changed`, or `Fixed` section.

## CLI documentation

When you add, remove, rename, or change any CLI command or its arguments/options
(anything under `build_cli()` in `src/e2e_ai/cli.py`), update the `## Commands`
section of the repository-root `README.md` in the same change:

- keep the command overview table in sync (add/remove rows), and
- update the per-command detail subsection (`### e2e-ai <command>`), documenting
  every argument and option — its name, whether it takes a value, its default,
  and what it does — plus the command's exit-code behavior when relevant.

Do not merge a CLI change that leaves the README out of date.

## Configuration

Project config lives in `e2e-ai.yml` (or `.e2e-ai.yml`) inside the target
repository. User defaults live at the platform config path returned by
`e2e_ai.config.default_user_config_path()`.

Merge order: user config is loaded first, then project config. Project values
override user defaults. Agent plugin entries merge by id.

## Development

- Activate `venv` before running commands.
- Run tests with `pytest -q`.
- Format with Ruff (`ruff check`, `ruff format`).
