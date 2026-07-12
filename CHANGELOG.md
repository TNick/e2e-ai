# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added

- `monitor:` project config section (`host`, `port`, `refresh_ms`, `open`) that
  supplies the defaults for `e2e-ai ui`; the CLI flags override it. Added to the
  fr-two `e2e-ai.yml`.

### Fixed

- Monitor dashboard no longer flickers every second: auto-refresh is gated on the
  state revision and skips the loading placeholder, so an idle view stays put.
- Launching a command from the monitor now transitions the open drawer straight
  to the command output, instead of briefly flashing the Commands page first.
- Monitor navigation now uses hash routing, so the browser address bar reflects
  the current page (`#active`, `#runs`, …) and back/forward and deep links work.
- Monitor tables are click-to-sort on any column (date/date-time columns sort by
  underlying value, not the displayed string), and Runs, Tests, and Agents add a
  status filter. Sort/filter choices persist across live refreshes.
- Broke an `isolation → integrations.fr_two → isolation` import cycle by loading
  the fr-two isolation backend lazily in the isolation registry.

- `e2e-ai ui` — a local, read-only web monitor (optional `monitor` extra:
  FastAPI/uvicorn). Browses runs/tests/attempts/failures/plans/agents from the
  read-only SQLite state database, infers live activity per shard/runner/
  environment, and launches allowlisted `e2e-ai` commands through validated
  argv (never a shell). New `e2e_ai.monitor` package (store, command registry,
  process launcher, FastAPI API, server) plus a bundled no-build static UI, so
  installed users need no Node.js at runtime. Options: `--host`, `--port`,
  `--refresh-ms`, `--db`, `--open`, `--project-root`.

- GitHub Actions publish workflow for PyPI releases, with a release-version
  guard that blocks publishing when the Git tag does not match
  `pyproject.toml`.
- `target_runtime` configuration and reusable Docker Compose startup for target
  projects. `e2e-ai run`, `verify` (run mode), and `repair` start configured
  support services, wait for health checks, then prepare isolation and run
  Playwright. Runtime logs live under `.e2e-ai/runs/<run-id>/runtime/`.
- fr-two `target_runtime` defaults and validation in the fr-two adapter; fr-two
  `e2e-ai.yml` declares the Docker lab stack startup contract.
- fr-two sequential runs use `shared_app_stack` so all slots target the single
  lab stack on `:8080`/`:8000` while keeping slot ids for storage isolation.
- `e2e-ai run --shard-min-tests N` runs until each fr-two slot has at least N
  passing tests.
- `e2e-ai verify` — the clean gate. Runs the full runnable suite once (no agents)
  and passes only when all tests are green, or with `--report <file|dir>` parses
  existing Playwright JSON reports (including sharded runs) and gates on them
  (`--allow-skips` to tolerate skipped tests).
- `e2e-ai cleanup` — drops isolation databases kept for debugging (via their
  `cleanup-manifest.json` records), with `--dry-run` preview and
  `--purge-artifacts` to also delete per-attempt work/run artifacts.

- fr-two migration (branch `e2eai`): the fr-two Makefile E2E targets, the legacy
  E2E scripts (`e2e_select`, `e2e_agent`, `e2e_agent_fix_loop`,
  `e2e_parallel_db_pool`, `assert_e2e_run_clean`), and their backend tests now
  route through `scripts/e2e_ai_bridge.py` / `e2e-ai`; the Playwright config and
  `dockerDb` helper honor the e2e-ai slot/report env contract and report the
  slot id + stable database name in connection errors.

### Fixed

- fr-two slot leases export `E2E_DATABASE_URL` with the Docker lab Postgres
  password so Playwright DB helpers (for example lab-directory login throttle
  cleanup) authenticate instead of failing with SCRAM errors.

- Wire the `fr_two` isolation backend into the factory so `e2e-ai run` and
  `repair` can lease stable fr-two execution slots instead of failing with
  "not implemented yet".
- Exclude patterns with a `tests/` prefix now match Playwright list output that
  omits that prefix from bare spec file names (e.g. fr-two `_diag-*` specs).

- Broke a `config -> mcp -> analysis -> runner/agents -> config` import cycle by
  making `e2e_ai.mcp` export its API lazily (PEP 562), so importing
  `e2e_ai.config`/`e2e_ai.cli` no longer fails.

- Project surface detection and agent edit scope: `target` config section with
  `frontend_only`, `full_stack`, and `frontend_with_backend_reference` scopes;
  layout heuristics in `e2e_ai.config.detect`; init scaffolding with
  `--target-scope` / path flags; validation of editable surfaces; target scope
  blocks in orchestrator prompts; and `BLOCKED_REFERENCE_BACKEND` planner
  stop condition.

- fr-two integration adapter (`e2e_ai.integrations.fr_two`): project detection,
  documented default config/validation, stable execution slots (fixed database
  name + user per slot), file/MinIO storage wipe, per-slot Docker Compose
  override rendering, a run manifest, and fr-two failure families (`map-filter`,
  `redlining`, `renderer`, `mapproxy`, `auth`, `backend`, `frontend-build`).
  Ships `examples/fr-two.e2e-ai.yml` and fixtures/tests. On the fr-two `e2eai`
  branch: a documented `e2e-ai.yml` and `scripts/e2e_ai_bridge.py` that routes
  fr-two E2E commands through the e2e-ai CLI.

- Repair-loop orchestrator (`e2e_ai.orchestrator`) with an explicit per-test
  state machine, external-blocker classification, instrumentation escalation
  policy, structured planner/implementer/instrumenter prompts built from
  `RepairContext`, repair-run store helpers, and `run_repair_loop` /
  `run_one_test_until_resolved` entry points.
- Extended `repair_policy` (and `repair` YAML alias) with
  `max_same_signature_attempts`, timeout/budget fields, and
  `require_external_blocker_for_successful_stop`.
- CLI repair options: `--test-id`, `--max-attempts`, and `--dry-run-agents`
  (plus `--dry-run` alias).
- `tests/orchestrator_test.py` covering state transitions, blocker
  classification, and loop behavior with fake backends and agents.
- Playwright MCP package (`e2e_ai.mcp`) for task-scoped browser inspection
  during repair: pinned `@playwright/mcp` argv builders, per-invocation session
  directories, tool allow/deny policy, Codex/Claude/Cursor client config
  rendering, artifact listing/redaction, and Node/npx health checks.
- `playwright_mcp` project config section with per-role enablement, tool
  policy, origin allowlists, and storage-state bootstrap settings.
- `RepairContext` identity fields (`logical_key`, `variant_key`,
  `test_list_selector`, `mcp_recommended`) and MCP prompt sections in the
  orchestrator.
- Agent request schemas and plugins now accept optional `AgentMcpAttachment`
  data; invocation manifests record MCP version, tools, and output paths.
- `tests/mcp_test.py` and MCP client-config fixtures under `tests/fixtures/mcp/`.

### Changed

- `e2e_ai.loop` now re-exports the orchestrator implementation; `FixLoop`
  delegates to the state-machine-driven repair loop.
- Repair-loop orchestrator attaches Playwright MCP to configured roles
  (instrumenter/implementer by default); required MCP setup failures classify
  as infrastructure blockers without consuming repair attempts.
- `e2e-ai doctor` and `e2e-ai agents doctor` report Playwright MCP readiness.
- Expanded agent plugin system: dedicated Codex, Claude, and Cursor plugins
  with safer default permission/sandbox flags, schema-constrained planner
  output, runtime capability discovery, quota snapshots with confidence
  levels, exit-class classification, role-based routing
  (planner/implementer/instrumenter), and quota reservation helpers.
- Routing config: `canary_cache_seconds`, `canary_task_class`,
  `planner_requires_schema`, and `schema_retry_limit`.
- `tests/agents_test.py` covering quota policy, exit classification, routing,
  and per-plugin command builders (no real CLIs required).
- Failure-context and instrumentation layer (`e2e_ai.analysis`): durable,
  secret-redacted `FailurePacket`s (error/stack normalization, stdout tails,
  screenshot/trace attachment paths, `error-context.md`, generic family
  classification with optional project classifier hook), grouping signatures,
  previous-failure/previous-plan context with size-bounded trimming, a
  second-pass instrumentation policy (`E2E_AI_TEMP_INSTRUMENTATION` marker), and
  serialized/atomic patch-artifact application under a working-tree lock. Every
  failed attempt now stores a rich failure packet the planner can use.

- Isolation backend package (`e2e_ai.isolation`) with a protocol-based
  interface, no-op backend for external stacks, PostgreSQL template-clone
  backend, Docker Compose argv helpers, port-range utilities, and a backend
  registry (`none`, `docker_postgres`, `docker_compose_postgres_template`,
  `fr_two`).
- `isolation.refresh_template` config (`auto` / `always` / `never`) and
  optional Postgres compose settings (`compose_project_name`, `env_file`,
  `long_lived_services`, `one_shot_services`).
- Repair loop integration: per-attempt environment leases, baseline
  preparation, cleanup policy (`keep_on_failure` / `keep_on_success`), and
  cleanup manifests for kept databases.

### Changed

- Typed configuration package with YAML loading, user/project merge, and
  validation for Playwright commands, agent plugins, isolation backends, and
  exclude patterns.
- Test inventory built from `playwright test --list` into a migration-versioned
  SQLite state database (`.e2e-ai/state.sqlite3`), with stable line-independent
  test ids, regex exclude patterns, and staleness tracking for vanished tests.
- Repair-history store (runs, attempts, failure packets, repair plans, agent
  invocations) layered on the same database, feeding prior failures/plans back
  into later attempts.
- Agent plugin system (`AgentPlugin` interface, `AgentSpec`, entry-point
  discovery) with built-in Claude/Codex/Cursor specs, cost/capability profiles,
  and token-free login checks.
- Sequential fix loop: run test → planner writes a plan → implementer applies it
  → rerun, escalating to an instrumenter on repeat failures, with per-test
  history and environmental-failure (BLOCKED) detection.
- Docker Postgres isolation backend: pristine template created once and cloned
  per test, dropped on success/re-run; reusable compose asset and
  `env_template` wiring. Configured via `isolation.postgres`.
- Playwright execution primitive (`e2e_ai.runner` package): per-attempt work
  directories (`.e2e-ai/work/<test-id>/<attempt-id>/`), unique JSON/blob report
  paths, a combined stdout+stderr log, exact-title `-g` reruns, command/
  environment manifests that redact secret values, JSON report parsing, and
  attempt-row persistence.
- CLI commands `discover`, `run` (with `--all`, `--test-id`, and `--fail-fast`),
  `repair`, `agents list`, `agents doctor`, and `db template`.
- `e2e-ai doctor` command to print resolved config paths and project id.
- `e2e-ai init` command to scaffold a starter `e2e-ai.yml` project config.
- Example project and user configuration files under `examples/`, plus a
  README and an fr-two migration plan under `docs/`.
