# Agent Guidelines

## Changelog

When you change production code in this repository, update the nearest
`CHANGELOG.md` (repository root) under `## [Unreleased]` with user-facing
bullets in the appropriate `Added`, `Changed`, or `Fixed` section.

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
