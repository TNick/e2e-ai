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
| `e2e-ai init` | Write a starter `e2e-ai.yml` with detected `target` scope. |
| `e2e-ai doctor` | Show resolved config, paths, and the isolation backend. |
| `e2e-ai discover` | Build/refresh the test catalog from `playwright test --list`. |
| `e2e-ai run --all` | Run each runnable test once and record results (no agents). Add `--fail-fast` to stop at the first failure. |
| `e2e-ai run --test-id <id>` | Run a single test by id (no agents). |
| `e2e-ai repair` | Run the full plan → implement → rerun fix loop. |
| `e2e-ai agents list` | List configured plugins and role assignments. |
| `e2e-ai agents doctor` | Check that the agents this project uses are logged in. |
| `e2e-ai db template` | Create/refresh the pristine Postgres template database. |

`repair` verifies that the selected agents are logged in before starting
(preferring a token-free check — a credentials file on disk or a `status`
subcommand) and fails fast if not. Pass `--skip-login-check` to bypass, or
`--dry-run` to run tests and pick agents without invoking them.

## Configuration

Configuration is layered; later layers win via a deep merge:

1. Built-in defaults.
2. **User config** at the platform config path
   (`e2e_ai.config.default_user_config_path()`) — personal defaults such as
   which agent binaries you have and enable.
3. **Project config** `e2e-ai.yml` (or `.e2e-ai.yml`) in the target repo — the
   source of truth for what is under test.

The `agents` section mixes **role assignments** (`planner`, `implementer`,
`instrumenter` → a plugin + optional profile) with **plugin definitions**
(`claude`, `codex`, `cursor` → `enabled` / `executable` overrides). Profiles
(`difficult` / `cheap`) pick a cost/capability tier within one CLI, so a planner
can run a stronger model than the implementer.

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
