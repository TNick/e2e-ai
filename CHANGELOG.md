# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added

- Runs record the master repair-loop process PID; on startup, orphaned
  ``running`` runs (missing PID or dead process) are marked ``stopped`` with
  reason ``process interrupted``, and open attempts are marked ``interrupted``.
  Pressing Ctrl+C during ``repair``, ``run``, or ``verify`` does the same for
  the active run immediately.
- ``e2e-ai cleanup --stale-runs`` to reconcile stale runs manually (also runs
  automatically when opening the state database for repair/run/verify/discover/ui).
- Monitor Tests page: **Runs** column shows total recorded executions per test,
  with failure packet count in parentheses when non-zero (e.g. ``5 (2)``).
- Monitor Tests detail drawer: failed attempts link to agent invocations
  (planner / implementer / instrumenter) for that run; full agent history is
  listed below the attempts table.

- Monitor Agents page: click an invocation to open its repair plan (planner),
  implementation log (implementer), or other agent output in the detail drawer.
  New ``GET /api/agents/{id}`` endpoint serves plan text and log files.
- ``e2e-ai repair --failed-only`` to repair only tests that did not pass in
  the previous finished run.
- Provider failover and multi-agent routing: per-role ordered provider pools
  (`routing.role_preferences`), automatic rotation to the next configured
  provider on retryable agent failures, per-invocation failover metadata in the
  state database and monitor UI, and `routing.failover` policy controls.
- Agent provider variants: configure multiple routable ids for one CLI via
  `provider` plus optional `model_candidates` / `reasoning_effort`. Variants
  resolve models from each CLI catalog at runtime and are skipped when no
  candidate is available.
- Project-level `routing.role_preferences` overrides in `e2e-ai.yml` so a
  target repository can define its own Cursor-first or other failover order.
- Agent plugin entries support optional `max_turns` to override Claude CLI
  turn budgets per variant or plugin definition.
- Opt-in live agent contract tests (`tests/live_agents_test.py`) gated by
  `E2E_AI_LIVE_AGENT_TESTS` invoke real Codex, Claude, and Cursor CLIs through
  the production plugin path.

### Changed

- Instrumentation escalation now counts only repair plans and repeated failure
  signatures from the current run; a new run always gets a plan-and-implement
  cycle before the instrumenter is invoked, even when prior runs left history.
- Ruff line length aligned to the project standard (80 columns); `make delint`
  and pre-commit now reformat code to that width instead of the previous
  88-column default. Remaining overlong docstrings, SQL, and string literals
  were wrapped manually so E501 is enforced in lint as well.
- Monitor agent detail drawer formats Codex ``--json`` stdout as one card per
  item, expands ``\\r\\n`` / ``\\n`` in command output, pretty-prints nested
  planner JSON, and collapses long ``aggregated_output`` blocks.
- Monitor agent detail drawer formats Cursor ``stream-json`` stdout the same
  way: merged thinking blocks, one card per tool call (read/grep/plan with
  collapsible output), collapsed user prompts, and session/result metadata.
- Repair loop prints prior attempt history as ``(N runs, M failures)`` when a
  test has been executed before (replacing the misleading ``(regression)`` /
  ``(seen before)`` labels).
- The repair loop prints `Starting Docker containers...` after scheduling when
  Docker Compose startup is required, so long container bootstraps are easier
  to understand.
- fr-two example config uses Cursor-first per-role failover
  (`cursor_auto` → model-specific Cursor variants → Claude → Codex) with
  runtime model resolution for GPT and Composer variants.

### Fixed

- Agent exit classification no longer treats Claude's benign
  `rate_limit_event` metadata as `quota_error` (word-boundary quota patterns).
- Claude `error_max_turns` responses are classified as `max_turns_exceeded`
  instead of `quota_error` or generic `task_failure`.
- Cursor workspace-trust prompts are classified as `permission_denied` and
  Cursor planner/instrumenter/implementer argv now always pass `--trust`
  (implementer also passes `--force`).
- Claude planner and instrumenter default turn budgets are raised (20 / 14) so
  investigation-heavy repair prompts are less likely to hit `error_max_turns`.
- Agent plugin model resolution no longer leaves a sentinel ``object()`` in
  argv when no ``model_candidates`` are configured.
- Successful agent invocations no longer show a misleading failover exit class
  (for example `quota_error`) when agent logs mention rate limits or usage caps
  but the process exited cleanly with status `ok`.
- Monitor Agents list: status cells now use green for `ok` and red for `error`,
  matching runs/tests coloring.
- `e2e-ai repair --failed-only` now ignores previous empty bookkeeping runs and
  no longer creates a new empty run when there are no failed tests to schedule.
- Repair attempt budgets now apply to the current run, so historical failures
  no longer cause `--failed-only` repairs to stop before invoking agents.
- Agent subprocesses no longer inherit parent stdin for argument/file prompt
  transports, and Codex now receives large repair prompts through an explicit
  stdin pipe instead of fragile command-line arguments.
- Regression failures can now escalate directly to instrumentation without
  crashing the repair state machine.
- Codex planner/implementer runs no longer fail on Windows when structured
  output is enabled: JSON Schema is written to a temporary file for
  ``codex exec --output-schema`` instead of passing inline JSON (which Codex
  treats as a file path).
- Codex instrumenter/implementer invocations no longer fail with
  ``unexpected argument '--ask-for-approval'``: approval policy is passed
  via ``-c approval_policy=...`` because current Codex CLI rejects the flag
  after ``exec``.
- Codex repair invocations use ``--ignore-user-config`` so personal MCP
  servers from ``~/.codex/config.toml`` do not load during unattended runs;
  Playwright MCP is layered through a per-invocation Codex profile instead.
- Provider failover now re-classifies agent output when a stored exit class
  is the generic ``task_failure``, so Codex usage-limit responses rotate to
  the next configured provider (for example Claude) instead of stopping.
- Logged-in agents with an unknown quota are now eligible as failover targets
  after a provider reports quota exhaustion.
- Claude planner, instrumenter, and implementer invocations now include
  ``--verbose``, as required by Claude CLI when using ``stream-json`` output.
- Cursor now receives repair prompts through stdin, avoiding Windows command
  length failures for large failure packets and agent context.
- When every planner provider exhausts its quota, repairs now stop as blocked
  after the current failed test execution rather than rerunning unchanged code
  and consuming the remaining repair-attempt budget.
- Oversized planner output is compacted before it is embedded in implementer
  prompts, preventing Codex from rejecting repair requests over its 1MiB input
  limit.
- Codex write-capable invocations on Windows use full access after the native
  workspace-write and unelevated sandboxes reject required file edits; planner
  invocations remain read-only.
- Agent log files include a millisecond suffix so concurrent invocations no
  longer overwrite each other's stdout logs.
- Codex Playwright-MCP runtime directories are now removed as directories on
  Windows, preventing repair runs from crashing during temporary-file cleanup.
- Repair loop no longer crashes with an invalid `planning` → `rerun` state
  transition when the planner agent fails after failover.
- Monitor UI closes the detail drawer when navigating to a different page, so
  run/test/failure details no longer cover the new view.
- Monitor Tests **Runs** column now stays in sync with attempt history (counts
  refresh while the detail drawer is open and when opening a test detail).

## [0.1.3] - 2026-07-12

### Changed

- Added extra dependency: httpx

## [0.1.2] - 2026-07-12

### Changed

- install monitor optional deps in ci
- add `httpx` dev dependency required by `fastapi.testclient` so the monitor API tests run in ci

## [0.1.1] - 2026-07-12

### Added

- `monitor:` project config section (`host`, `port`, `refresh_ms`, `open`) that supplies the defaults for `e2e-ai ui`; the CLI flags override it. Added to the fr-two `e2e-ai.yml`.

### Changed

- `e2e_ai.loop` now re-exports the orchestrator implementation; `FixLoop` delegates to the state-machine-driven repair loop.
- Repair-loop orchestrator attaches Playwright MCP to configured roles (instrumenter/implementer by default); required MCP setup failures classify as infrastructure blockers without consuming repair attempts.
- `e2e-ai doctor` and `e2e-ai agents doctor` report Playwright MCP readiness.
- Expanded agent plugin system: dedicated Codex, Claude, and Cursor plugins with safer default permission/sandbox flags, schema-constrained planner output, runtime capability discovery, quota snapshots with confidence levels, exit-class classification, role-based routing (planner/implementer/instrumenter), and quota reservation helpers.
- Routing config: `canary_cache_seconds`, `canary_task_class`, `planner_requires_schema`, and `schema_retry_limit`.
- `tests/agents_test.py` covering quota policy, exit classification, routing, and per-plugin command builders (no real CLIs required).
- Failure-context and instrumentation layer (`e2e_ai.analysis`): durable, secret-redacted `FailurePacket`s (error/stack normalization, stdout tails, screenshot/trace attachment paths, `error-context.md`, generic family classification with optional project classifier hook), grouping signatures, previous-failure/previous-plan context with size-bounded trimming, a second-pass instrumentation policy (`E2E_AI_TEMP_INSTRUMENTATION` marker), and serialized/atomic patch-artifact application under a working-tree lock. Every failed attempt now stores a rich failure packet the planner can use.
- Isolation backend package (`e2e_ai.isolation`) with a protocol-based interface, no-op backend for external stacks, PostgreSQL template-clone backend, Docker Compose argv helpers, port-range utilities, and a backend registry (`none`, `docker_postgres`, `docker_compose_postgres_template`, `fr_two`).
- `isolation.refresh_template` config (`auto` / `always` / `never`) and optional Postgres compose settings (`compose_project_name`, `env_file`, `long_lived_services`, `one_shot_services`).
- Repair loop integration: per-attempt environment leases, baseline preparation, cleanup policy (`keep_on_failure` / `keep_on_success`), and cleanup manifests for kept databases.
- Typed configuration package with YAML loading, user/project merge, and validation for Playwright commands, agent plugins, isolation backends, and exclude patterns.
- Test inventory built from `playwright test --list` into a migration-versioned SQLite state database (`.e2e-ai/state.sqlite3`), with stable line-independent test ids, regex exclude patterns, and staleness tracking for vanished tests.
- Repair-history store (runs, attempts, failure packets, repair plans, agent invocations) layered on the same database, feeding prior failures/plans back into later attempts.
- Agent plugin system (`AgentPlugin` interface, `AgentSpec`, entry-point discovery) with built-in Claude/Codex/Cursor specs, cost/capability profiles, and token-free login checks.
- Sequential fix loop: run test → planner writes a plan → implementer applies it → rerun, escalating to an instrumenter on repeat failures, with per-test history and environmental-failure (BLOCKED) detection.
- Docker Postgres isolation backend: pristine template created once and cloned per test, dropped on success/re-run; reusable compose asset and `env_template` wiring. Configured via `isolation.postgres`.
- Playwright execution primitive (`e2e_ai.runner` package): per-attempt work directories (`.e2e-ai/work/<test-id>/<attempt-id>/`), unique JSON/blob report paths, a combined stdout+stderr log, exact-title `-g` reruns, command/ environment manifests that redact secret values, JSON report parsing, and attempt-row persistence.
- CLI commands `discover`, `run` (with `--all`, `--test-id`, and `--fail-fast`), `repair`, `agents list`, `agents doctor`, and `db template`.
- `e2e-ai doctor` command to print resolved config paths and project id.
- `e2e-ai init` command to scaffold a starter `e2e-ai.yml` project config.
- Example project and user configuration files under `examples/`, plus a README and an fr-two migration plan under `docs/`.
- Initial implementation
- Run GitHub tests on push
- e2e-ai verify and e2e-ai cleanup implemented
- We're now able to run a project-specific startup
- Add ui/monitoring
- Update ui

### Fixed

- Monitor Tests **Runs** column now matches attempt history: counts use a joined
  aggregate query, refresh while the detail drawer is open, and update when a
  test detail is opened.

- Monitor dashboard no longer flickers every second: auto-refresh is gated on the state revision and skips the loading placeholder, so an idle view stays put.
- Launching a command from the monitor now transitions the open drawer straight to the command output, instead of briefly flashing the Commands page first.
- Monitor navigation now uses hash routing, so the browser address bar reflects the current page (`#active`, `#runs`, …) and back/forward and deep links work.
- Monitor tables are click-to-sort on any column (date/date-time columns sort by underlying value, not the displayed string), and Runs, Tests, and Agents add a status filter. Sort/filter choices persist across live refreshes.
- Monitor Runs/Tests/Failures/Agents/Settings pages have an ⓘ info button next to the title that reveals (with a transition, rotating the icon 15°) a description of the page and every column and its special values.
- Monitor Settings has a "Show all settings" toggle that reveals the full effective configuration (defaults + user config + project config) via a new read-only `GET /api/config` endpoint.
- Broke an `isolation → integrations.fr_two → isolation` import cycle by loading the fr-two isolation backend lazily in the isolation registry.
- `e2e-ai ui` — a local, read-only web monitor (optional `monitor` extra: FastAPI/uvicorn). Browses runs/tests/attempts/failures/plans/agents from the read-only SQLite state database, infers live activity per shard/runner/ environment, and launches allowlisted `e2e-ai` commands through validated argv (never a shell). New `e2e_ai.monitor` package (store, command registry, process launcher, FastAPI API, server) plus a bundled no-build static UI, so installed users need no Node.js at runtime. Options: `--host`, `--port`, `--refresh-ms`, `--db`, `--open`, `--project-root`.
- GitHub Actions publish workflow for PyPI releases, with a release-version guard that blocks publishing when the Git tag does not match `pyproject.toml`.
- `target_runtime` configuration and reusable Docker Compose startup for target projects. `e2e-ai run`, `verify` (run mode), and `repair` start configured support services, wait for health checks, then prepare isolation and run Playwright. Runtime logs live under `.e2e-ai/runs/<run-id>/runtime/`.
- fr-two `target_runtime` defaults and validation in the fr-two adapter; fr-two `e2e-ai.yml` declares the Docker lab stack startup contract.
- fr-two sequential runs use `shared_app_stack` so all slots target the single lab stack on `:8080`/`:8000` while keeping slot ids for storage isolation.
- `e2e-ai run --shard-min-tests N` runs until each fr-two slot has at least N passing tests.
- `e2e-ai verify` — the clean gate. Runs the full runnable suite once (no agents) and passes only when all tests are green, or with `--report <file|dir>` parses existing Playwright JSON reports (including sharded runs) and gates on them (`--allow-skips` to tolerate skipped tests).
- `e2e-ai cleanup` — drops isolation databases kept for debugging (via their `cleanup-manifest.json` records), with `--dry-run` preview and `--purge-artifacts` to also delete per-attempt work/run artifacts.
- fr-two migration (branch `e2eai`): the fr-two Makefile E2E targets, the legacy E2E scripts (`e2e_select`, `e2e_agent`, `e2e_agent_fix_loop`, `e2e_parallel_db_pool`, `assert_e2e_run_clean`), and their backend tests now route through `scripts/e2e_ai_bridge.py` / `e2e-ai`; the Playwright config and `dockerDb` helper honor the e2e-ai slot/report env contract and report the slot id + stable database name in connection errors.
- fr-two slot leases export `E2E_DATABASE_URL` with the Docker lab Postgres password so Playwright DB helpers (for example lab-directory login throttle cleanup) authenticate instead of failing with SCRAM errors.
- Wire the `fr_two` isolation backend into the factory so `e2e-ai run` and `repair` can lease stable fr-two execution slots instead of failing with "not implemented yet".
- Exclude patterns with a `tests/` prefix now match Playwright list output that omits that prefix from bare spec file names (e.g. fr-two `_diag-*` specs).
- Broke a `config -> mcp -> analysis -> runner/agents -> config` import cycle by making `e2e_ai.mcp` export its API lazily (PEP 562), so importing `e2e_ai.config`/`e2e_ai.cli` no longer fails.
- Project surface detection and agent edit scope: `target` config section with `frontend_only`, `full_stack`, and `frontend_with_backend_reference` scopes; layout heuristics in `e2e_ai.config.detect`; init scaffolding with `--target-scope` / path flags; validation of editable surfaces; target scope blocks in orchestrator prompts; and `BLOCKED_REFERENCE_BACKEND` planner stop condition.
- fr-two integration adapter (`e2e_ai.integrations.fr_two`): project detection, documented default config/validation, stable execution slots (fixed database name + user per slot), file/MinIO storage wipe, per-slot Docker Compose override rendering, a run manifest, and fr-two failure families (`map-filter`, `redlining`, `renderer`, `mapproxy`, `auth`, `backend`, `frontend-build`). Ships `examples/fr-two.e2e-ai.yml` and fixtures/tests. On the fr-two `e2eai` branch: a documented `e2e-ai.yml` and `scripts/e2e_ai_bridge.py` that routes fr-two E2E commands through the e2e-ai CLI.
- Repair-loop orchestrator (`e2e_ai.orchestrator`) with an explicit per-test state machine, external-blocker classification, instrumentation escalation policy, structured planner/implementer/instrumenter prompts built from `RepairContext`, repair-run store helpers, and `run_repair_loop` / `run_one_test_until_resolved` entry points.
- Extended `repair_policy` (and `repair` YAML alias) with `max_same_signature_attempts`, timeout/budget fields, and `require_external_blocker_for_successful_stop`.
- CLI repair options: `--test-id`, `--max-attempts`, and `--dry-run-agents` (plus `--dry-run` alias).
- `tests/orchestrator_test.py` covering state transitions, blocker classification, and loop behavior with fake backends and agents.
- Playwright MCP package (`e2e_ai.mcp`) for task-scoped browser inspection during repair: pinned `@playwright/mcp` argv builders, per-invocation session directories, tool allow/deny policy, Codex/Claude/Cursor client config rendering, artifact listing/redaction, and Node/npx health checks.
- `playwright_mcp` project config section with per-role enablement, tool policy, origin allowlists, and storage-state bootstrap settings.
- `RepairContext` identity fields (`logical_key`, `variant_key`, `test_list_selector`, `mcp_recommended`) and MCP prompt sections in the orchestrator.
- Agent request schemas and plugins now accept optional `AgentMcpAttachment` data; invocation manifests record MCP version, tools, and output paths.
- `tests/mcp_test.py` and MCP client-config fixtures under `tests/fixtures/mcp/`.

[0.1.1]: https://github.com/TNick/e2e-ai/compare/0816d060c8d69ee8f8a0b9fd374f6ac9a8248212...v0.1.1
[0.1.2]: https://github.com/TNick/e2e-ai/compare/v0.1.1...v0.1.2
[0.1.3]: https://github.com/TNick/e2e-ai/compare/v0.1.2...v0.1.3
[unreleased]: https://github.com/TNick/e2e-ai/compare/v0.1.3...HEAD
