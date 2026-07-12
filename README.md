# e2e-ai

An AI-driven loop that runs a project's Playwright end-to-end tests
sequentially and keeps working until **every test is green** — or until the
remaining failures are judged to be setup/environment problems outside local
control.

For each failing test, e2e-ai asks a *planner* agent to write a fix plan, has a
cheaper *implementer* agent apply it, and re-runs the test. If it still fails, a
smarter *instrumenter* agent adds diagnostics and proposes a deeper fix. Every
attempt, failure, and plan is recorded so later attempts (and regressions) get
the full history of what has already been tried.

## How it works

```
discover ─►  playwright test --list  ─►  catalog (SQLite, gitignored)
             │
repair   ─►  for each non-passing test, in order:
             ├─ (docker) clone a fresh Postgres DB from a pristine template
             ├─ run the test in isolation
             ├─ pass?  → record, drop the DB, next test
             └─ fail?  → planner writes a plan  → implementer applies it → rerun
                         still failing? → instrumenter adds diagnostics → rerun
                         looks environmental? → mark BLOCKED and move on
```

The loop stops when the scheduled tests are all passing, or (optionally) at the
first unsolvable failure.

## Install

```bash
pip install -e .        # from a checkout
```

Requires Python ≥ 3.11. Agent CLIs (`claude`, `codex`, `cursor-agent`) must be
installed and logged in separately for the `repair` command.

## Quick start

```bash
cd /path/to/your/project
e2e-ai init                 # write a starter e2e-ai.yml
e2e-ai doctor               # show resolved config + paths
e2e-ai discover             # build the test catalog
e2e-ai run                  # run each pending test once (no fixing)
e2e-ai repair               # run the AI fix loop until green
```

`e2e-ai --version` and `e2e-ai --help` print the version and command help.

## Commands

| Command | Purpose |
| --- | --- |
| `e2e-ai` | Run `repair` with the same options as `e2e-ai repair`. |
| `e2e-ai init` | Write a starter `e2e-ai.yml` with detected `target` scope. |
| `e2e-ai doctor` | Show resolved config, paths, isolation backend, and target runtime. |
| `e2e-ai discover` | Build/refresh the test catalog from `playwright test --list`. |
| `e2e-ai run --all` | Run each runnable test once and record results (no agents). Add `--fail-fast` to stop at the first failure. |
| `e2e-ai run --test-id <id>` | Run a single test by id (no agents). |
| `e2e-ai repair` | Run the full plan → implement → rerun fix loop. |
| `e2e-ai verify` | The clean gate: run the suite once (or parse existing reports) and pass only when clean. |
| `e2e-ai cleanup` | Drop kept isolation databases, reconcile stale runs, and (optionally) purge artifacts. |
| `e2e-ai agents list` | List configured plugins and role assignments. |
| `e2e-ai agents doctor` | Check that the agents this project uses are logged in. |
| `e2e-ai db template` | Create/refresh the pristine Postgres template database. |
| `e2e-ai ui` | Start a local, read-only web monitor for the state database. |

`repair` verifies that the selected agents are logged in before starting
(preferring a token-free check — a credentials file on disk or a `status`
subcommand) and fails fast if not. Pass `--skip-login-check` to bypass, or
`--dry-run` to run tests and pick agents without invoking them.

### Global options

These apply to the top-level `e2e-ai` command (before the subcommand):

- `--version` — print the installed version and exit.
- `-v`, `--verbose` — increase logging verbosity. Repeatable: `-v` = INFO,
  `-vv` = DEBUG. Default is WARNING.
- `--help` — show help. Available on every command and subgroup.

Most subcommands accept `--project-root DIRECTORY` (default `.`): the directory
treated as the target project root. e2e-ai discovers `e2e-ai.yml` by walking up
from here and writes all state under `<project-root>/.e2e-ai/`. (`init` is the
exception — it always scaffolds into the current working directory.)

### `e2e-ai init`

Scaffold a starter `e2e-ai.yml` in the **current directory**, detecting a
sensible `target` edit scope from the repo layout.

- `--force` — overwrite an existing `e2e-ai.yml`. Without it, the command
  refuses to clobber an existing file.
- `--target-scope [frontend-only|full-stack|frontend-with-backend-reference]` —
  set the declared edit scope for repair agents instead of using the detected
  one. `frontend-only` = agents edit only the frontend surface;
  `full-stack` = frontend and backend are both editable;
  `frontend-with-backend-reference` = frontend is editable, backend is
  read-only for diagnosis.
- `--frontend-path TEXT` — frontend surface path, relative to the project root.
- `--backend-path TEXT` — backend surface path, relative to the project root.
- `--backend-reference` — shortcut that keeps the backend read-only (implies
  `frontend-with-backend-reference`).

If the layout is ambiguous and no scope flag is given, `init` prompts for the
scope interactively.

### `e2e-ai doctor`

Print the resolved configuration and environment so you can confirm e2e-ai sees
what you expect: project id, the project/user config paths, project root, state
dir, isolation backend, target runtime (Docker Compose files/services/health
checks, and any missing compose files), number of exclude patterns, the target
edit scope and its surfaces, and Playwright MCP status (with a health probe when
MCP is enabled).

- `--project-root DIRECTORY` — project root to inspect (default `.`).

### `e2e-ai discover`

Run `playwright test --list` and sync the discovered tests into the SQLite
inventory (`.e2e-ai/state.sqlite3`), applying `exclude` patterns and marking
vanished tests stale. Prints counts (discovered / runnable / excluded / stale)
and the state database path. This is the source of the test inventory for
`run`, `repair`, and `verify`.

- `--project-root DIRECTORY` — project root to inspect (default `.`).

### `e2e-ai run`

Run runnable tests once each and record the results **without** invoking repair
agents. You must pass either `--all` or `--test-id`. Exits `0` when every
scheduled test is green, `1` otherwise.

- `--project-root DIRECTORY` — project root (default `.`).
- `--test-id TEXT` — run only the test with this id (from `discover`). Errors if
  no runnable test has that id.
- `--all` — run all runnable tests.
- `--fail-fast` — stop at the first failing test instead of continuing through
  the whole suite (the default is to continue).
- `--limit INTEGER` — run only the first N runnable tests (in deterministic
  order).
- `--rediscover` / `--no-rediscover` — refresh the inventory before running
  (default: `--rediscover`). Use `--no-rediscover` to reuse the last catalog.
- `--start-runtime` / `--no-start-runtime` — start the configured target Docker
  support stack (and wait for health checks) before running (default:
  `--start-runtime`). Use `--no-start-runtime` when the stack is already up.
- `--shard-min-tests INTEGER` — keep running until each execution slot has at
  least N passing tests (used by the fr-two shared-stack slot model).

### `e2e-ai repair`

Run the full AI fix loop: run a test, and on failure have the planner produce a
plan, the implementer apply it, and re-run — escalating to the instrumenter on
repeat failures — until each test is green or judged externally blocked. Before
starting it verifies that the selected agents are logged in. Exits `0` when all
scheduled tests end green, `1` otherwise.

The top-level `e2e-ai` command is shorthand for `e2e-ai repair`, so the same
repair options are available either way.

- `--project-root DIRECTORY` — project root (default `.`).
- `--limit INTEGER` — repair only the first N runnable tests.
- `--test-id TEXT` — repair only this test id.
- `--max-attempts INTEGER` — override `repair_policy.max_attempts_per_test` for
  this run.
- `--rediscover` / `--no-rediscover` — refresh the inventory before repairing
  (default: `--rediscover`).
- `--skip-login-check` — do not verify agent logins before starting (use when
  you know the CLIs are authenticated, or in the dry-run modes).
- `--dry-run-agents` — build failure packets and agent prompts but do **not**
  invoke the agent CLIs (no tokens spent). Useful for inspecting what the loop
  would send.
- `--dry-run` — alias for `--dry-run-agents`.
- `--failed-only` — repair only tests that did not pass in the most recent
  finished run with recorded attempts (for example after `e2e-ai run --all`).
  Tests that were not part of that run are skipped. When every test passed in
  the previous run, repair exits successfully without scheduling anything.
- `--start-runtime` / `--no-start-runtime` — start the target Docker support
  stack before running (default: `--start-runtime`).

### `e2e-ai verify`

The final clean gate. In **run mode** (default) it runs the full runnable suite
once with no agents and passes only when every test is green. In **report
mode** (`--report`) it parses existing Playwright JSON reports — including
sharded runs — and gates on their combined stats without running anything. A run
is clean when there are no unexpected failures, no flaky results, at least one
expected pass, and (unless `--allow-skips`) no skipped tests. Exits `0` when
clean, `1` otherwise.

- `--project-root DIRECTORY` — project root (default `.`); ignored in report
  mode.
- `--report PATH` — gate an existing Playwright JSON report. May be a single
  `.json` file or a directory (searched recursively for `*.json`, so a folder of
  shard reports is summed). Repeatable — pass `--report` multiple times to
  combine sources. When omitted, verify runs the suite itself.
- `--allow-skips` — do not fail the gate on skipped tests.
- `--rediscover` / `--no-rediscover` — refresh the inventory before running
  (run mode only; default `--rediscover`).
- `--limit INTEGER` — run only the first N tests (run mode only).
- `--start-runtime` / `--no-start-runtime` — start the target Docker support
  stack before running (run mode only; default `--start-runtime`).

### `e2e-ai cleanup`

Reclaim resources left by kept environments. Databases retained for debugging
(via `isolation.keep_on_failure` / `keep_on_success`) leave a
`cleanup-manifest.json` under the state dir; cleanup finds each one and drops the
recorded database. By default it leaves per-attempt artifacts in place (so kept
evidence is not lost). Always exits `0`.

Repair, run, verify, and ui also reconcile stale runs automatically on
startup: any run still marked `running` whose master PID is missing or no longer
alive is marked `stopped` with reason `process interrupted`, and open attempts
for that run are marked `interrupted`. Pressing Ctrl+C during an active repair
or run loop marks the current run the same way before exiting with code `130`.

- `--project-root DIRECTORY` — project root (default `.`).
- `--dry-run` — list what would be dropped/removed without changing anything.
- `--stale-runs` — mark orphaned `running` repair runs as stopped when their
  master process no longer exists (also runs automatically when opening the
  state database for repair/run/verify/discover/ui).
- `--purge-artifacts` — also delete the per-attempt `work/` and `runs/`
  directories under the state dir. Destructive — this removes logs, reports, and
  agent transcripts.

### `e2e-ai db template`

Create (or refresh) the pristine PostgreSQL template database that per-test
clones are made from. Only valid when the isolation backend is a Postgres
template-clone backend; otherwise it errors.

- `--project-root DIRECTORY` — project root (default `.`).
- `--refresh` — recreate the template even if it already exists (rebuild it from
  the current seeded source database).

### `e2e-ai agents list`

List the configured agents: role assignments (`planner` / `implementer` /
`instrumenter` → plugin, with any profile) and plugin definitions (enabled state
and resolved executable).

- `--project-root DIRECTORY` — project root (default `.`).

### `e2e-ai agents doctor`

Check login/health for the agents this project actually uses. For each agent it
reports whether it is logged in (preferring a token-free check — a credentials
file on disk or a `status` subcommand) and whether that could be verified
without spending tokens. When Playwright MCP is enabled it also probes MCP
readiness. Exits `1` if any required agent is not logged in, `0` otherwise.

- `--project-root DIRECTORY` — project root (default `.`).

### `e2e-ai ui`

Start a **local, read-only web monitor** for the state database. It serves a
bundled dashboard (no Node.js required at runtime) that browses runs, tests,
attempts, failures, plans, and agent invocations, shows live activity per
shard/runner/environment, and can launch the other `e2e-ai` commands through
validated option forms (never a shell). Database access is strictly read-only.

Requires the optional monitor extra: `pip install "e2e-ai[monitor]"`. Without it
the command exits with an install hint.

`--host`, `--port`, and `--refresh-ms` default to the project config's
`monitor:` section (below), falling back to the built-in defaults; the CLI flags
override the config.

```yaml
monitor:
  host: 127.0.0.1   # bind address; non-loopback prints a warning
  port: 8765        # server port
  refresh_ms: 1000  # live-refresh interval hint
  open: false       # open a browser on start
```

- `--project-root DIRECTORY` — project root (default `.`); used to resolve the
  config and (unless `--db` is given) the state database path.
- `--host TEXT` — interface to bind (default: `monitor.host`, else `127.0.0.1`).
  Binding to anything other than a loopback address prints a warning, since the
  monitor has no authentication yet.
- `--port INTEGER` — port to serve on (default: `monitor.port`, else `8765`).
- `--refresh-ms INTEGER` — live-refresh interval hint in milliseconds passed to
  the UI and the `/api/events` stream (default: `monitor.refresh_ms`, else
  `1000`).
- `--db PATH` — explicit state database path. When given, the config is optional
  (the monitor points directly at that database). When omitted, the path is
  taken from `database_path(config)`.
- `--open` — open the dashboard in the default browser after the server starts
  (uses `webbrowser.open`, never a shell). Also enabled by `monitor.open: true`.

The command prints the local URL and the resolved state database path, then
serves until interrupted (Ctrl-C). Commands launched from the UI run as
`python -m e2e_ai <subcommand> …` with the same project root, and their output
and status are recorded under `.e2e-ai/monitor/`. The dashboard only re-renders
when the state actually changes, so an idle view does not flicker.

## Configuration

Configuration is layered; later layers win via a deep merge:

1. Built-in defaults.
2. **User config** at the platform config path
   (`e2e_ai.config.default_user_config_path()`) — personal defaults such as
   which agent binaries you have and enable.
3. **Project config** `e2e-ai.yml` (or `.e2e-ai.yml`) in the target repo — the
   source of truth for what is under test.

The `agents` section mixes **role assignments** (`planner`, `implementer`,
`instrumenter` → a plugin or variant + optional profile), **plugin definitions**
(`claude`, `codex`, `cursor` → `enabled` / `executable` overrides), and
**provider variants** (custom ids with `provider` + optional `model_candidates`
/ `reasoning_effort` / `max_turns`). Variants let one CLI appear multiple times in a failover
list — for example `cursor_auto` (no explicit model) and `cursor_gpt` (GPT
planner model). Profiles (`difficult` / `cheap`) still pick a cost/capability
tier within one CLI when no variant model is configured.

`routing` settings control provider selection and failover. User config holds
defaults; project `e2e-ai.yml` may override `routing.role_preferences` for
that repository:

- `role_preferences` — ordered provider or variant ids per role (`planner`,
  `implementer`, `instrumenter`). When a provider fails for a retryable reason
  (quota, auth, timeout, schema, empty output, no-op implementation,
  max-turns exceeded, permission denied), the repair loop rotates to the next
  repair loop rotates to the next entry in the list for that role.
- `failover.enabled` — turn provider rotation on or off (default: on).
- `failover.max_switches_per_test` — cap provider switches per test so a bad
  environment cannot loop forever (default: `6`).
- `failover.retryable_exit_classes` — optional override for which normalized
  exit classes trigger a switch.

Variant `model_candidates` are matched against each CLI's runtime model catalog
when available; variants with no resolvable model are skipped during selection.
`max_turns` overrides Claude `--max-turns` for planner/instrumenter roles when
set on a plugin or variant entry.

The `target` section declares which parts of the repository repair agents may
edit:

- `frontend_only` — only the frontend surface (default when `target` is omitted).
- `full_stack` — editable frontend and backend surfaces for coordinated fixes.
- `frontend_with_backend_reference` — frontend edits allowed; backend is
  read-only for diagnosis (planner may stop with `BLOCKED_REFERENCE_BACKEND`).

`e2e-ai init` detects common frontend/backend layouts and writes a starter
`target` block. Override with `--target-scope`, `--frontend-path`,
`--backend-path`, or `--backend-reference`.

Exclude patterns are **regular expressions** matched against each test's
selector, spec file, title, and id. See [`examples/`](examples/) for a full
project config and a user config.

## Docker / per-test database isolation

Set `isolation.backend: docker_compose_postgres_template` to give every test a
clean database:

* A pristine **template** is created **once** from your seeded `source_db`
  (`e2e-ai db template`).
* Every test **clones** the template into its own database (near-instant, no
  container churn). A passing test **drops** its clone; a re-run **drops** the
  previous clone first.
* `isolation.postgres.env_template` maps env var names to value templates
  (`{database}` → the clone name) so the app under test connects to its clone.

A reusable Postgres compose service ships at
`e2e_ai/docker/assets/compose.postgres.yml`.

## Extending with your own agent

Agents implement the `e2e_ai.agents.base.AgentPlugin` interface. The built-ins
are declarative `AgentSpec`s driven by a generic CLI runner. Register a new
agent through the `e2e_ai.agents` entry-point group (it may yield an `AgentSpec`
or an `AgentPlugin`).

## State

All local state lives under the gitignored `state.dir` (default `.e2e-ai`):

- `state.sqlite3` — the migration-versioned state database. It holds the test
  **inventory** (`projects`, `tests`) plus the repair **history** (`runs`,
  `attempts`, `failure_packets`, `repair_plans`, `agent_invocations`), so later
  attempts and regressions get the full history of what was already tried.
- `work/<test-id>/<attempt-id>/` — per-attempt artifacts: the combined
  `output.log`, `playwright-results.json` (unique per attempt), and
  `command.json`/`environment.json` manifests (secret values redacted).
- `runs/<test-id>/agents/` — per-test agent invocation logs.

Discovery assigns each test a stable id (independent of line numbers), so a
test keeps its id — and its history — as the suite evolves. Missing tests are
marked stale rather than deleted.

## Publishing A New Version

The release path is **distlift** -> pushed `v*` tag -> GitHub Actions ->
GitHub Release + PyPI Trusted Publishing. The repository does not use a
long-lived PyPI API token.

Release settings live in `[tool.distlift]` inside `pyproject.toml` (simple
Python mode, `v{version}` tags, `origin` remote).

Before publishing a new version, a developer should:

1. Run the local checks:

   ```bash
   python -m ruff check src tests
   python -m pytest -q
   python -m build
   ```

2. Run distlift from the repository root (with a clean working tree). Pick
   **one** version selector:

   ```bash
   distlift --version 0.1.2
   distlift --patch
   distlift --minor
   distlift --major
   ```

   Use `--dry-run` first to preview the plan. Distlift updates
   `pyproject.toml`, refreshes `CHANGELOG.md`, commits, creates the
   `v0.1.2` tag, and pushes the commit and tag to `origin`.

3. Pushing the tag triggers `.github/workflows/publish.yml`. The workflow
   runs tests again, checks that the tag matches `pyproject.toml`, builds
   distributions, creates the GitHub Release automatically, and uploads to
   PyPI with trusted publishing.

If the release tag does not match `pyproject.toml`, the workflow fails before
uploading anything.

PyPI and GitHub must also be configured once by a maintainer:

- Add a PyPI Trusted Publisher for this repository, the `publish.yml` workflow,
  and the `pypi` environment.
- Create the GitHub `pypi` environment and protect it with maintainer review.
- Keep `id-token: write` limited to the publish job only.
- Allow the workflow to create GitHub Releases (`contents: write` on the
  `create-release` job only).
