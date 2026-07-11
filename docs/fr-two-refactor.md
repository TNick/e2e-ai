# Refactoring fr-two to use e2e-ai

`fr-two` (`D:\prog\CadPlatf\fr-two`) already has a bespoke, well-developed
AI e2e fix loop under `scripts/` and `e2e/`. e2e-ai generalizes that machinery
into a standalone, reusable tool. This document is the migration plan.

## What fr-two has today

| fr-two piece | Responsibility | e2e-ai replacement |
| --- | --- | --- |
| `scripts/e2e_catalog.py` | Hand-built catalog of tests/tasks | `e2e_ai.catalog` (driven by `playwright test --list`) |
| `scripts/e2e_agent_fix_loop.py` | Backends, prompts, dispatch, acceptance | `e2e_ai.loop` + `e2e_ai.agents` + `e2e_ai.planner` |
| `scripts/e2e_agent.py` | Lab stack bring-up / login probes | Project-side setup (kept) + `isolation` backend |
| `scripts/e2e_parallel_db_pool.py` | Postgres template + per-shard clones | `e2e_ai.docker.postgres` (per-test clones) |
| `scripts/e2e_select.py` | Test selection | `catalog.exclude` + scheduling |
| `e2e/helpers/dockerDb.ts` | Per-shard DB wiring in tests | `isolation.postgres.env_template` |
| `AGENT_BACKENDS_FILE` (JSON) | codex/claude/copilot configs | `agents` section of `e2e-ai.yml` |

The concepts map almost one-to-one: fr-two's backend config
(`prompt_command` + `prompt_transport`, health commands, codex/claude/copilot
quirks) is exactly what `e2e_ai.agents.AgentSpec` + the built-in specs encode.

## Target end state

fr-two keeps only what is genuinely project-specific — bringing up its Docker
lab stack and seeding data — and delegates catalog, scheduling, agent dispatch,
prompting, and per-test DB isolation to e2e-ai.

## Migration steps

1. **Add e2e-ai as a dev dependency** of the tooling env used for e2e
   (`pip install e2e-ai`). It only needs `click` + `PyYAML` at runtime.

2. **Author `fr-two/e2e-ai.yml`.** Start from
   [`examples/fr-two.e2e-ai.yml`](../examples/fr-two.e2e-ai.yml):
   - `playwright.cwd: e2e`, `list_command`/`run_command` = `pnpm exec playwright
     test [--list]`.
   - Port the existing exclusions (meta/aggregate keys, `_diag-*` specs) into
     `exclude.tests` **as regexes**.
   - `agents`: map `planner → codex (difficult)`, `implementer → codex (cheap)`,
     `instrumenter → claude (difficult)`. Copilot can be added later as a
     custom plugin if still needed.

3. **Move DB isolation to `isolation.postgres`.** fr-two's
   `e2e_parallel_db_pool.py` already does `CREATE DATABASE … WITH TEMPLATE`
   against the bundled `postgres` service — the same mechanism e2e-ai uses. Set:
   - `source_db` = the seeded lab DB (`frtwo`),
   - `template_db` = `e2e_ai_pristine`,
   - `env_template` = the vars QGIS/backend read for the DB name (what
     `dockerDb.ts` writes into the per-shard pg service file).
   Then `e2e-ai db template` builds the pristine template once; each test clones
   it. This replaces the shard pool with per-test clones (simpler isolation; no
   shard bookkeeping). Keep `keep_on_failure: true` to preserve a failed test's
   DB for debugging.

4. **Keep lab bring-up as a pre-step.** The logic in `e2e_agent.py`
   (`ensure_e2e_lab_stack`, health/login probes, mapproxy config) stays in
   fr-two. Run it before `e2e-ai repair` (e.g. `make e2e-ai` →
   `python scripts/e2e_stack_up.py && e2e-ai repair`). e2e-ai's environmental
   detection will mark a test BLOCKED (not "code") if the stack is down, which
   is the correct signal to fix setup rather than burn agent time.

5. **Fold fr-two's prompt guidance into config-adjacent docs.** The rich,
   fr-two-specific rules in `build_issue_prompt` (qwc2 read-only except raw
   `console.*`, rebuild the webpack bundle, read the backend container log) are
   project knowledge. Put them in `fr-two/AGENTS.md`; e2e-ai's prompts already
   instruct agents to read `AGENTS.md`/`README.md`, so that guidance still
   reaches every agent without living in the generic tool.

6. **Retire the superseded scripts** once parity is confirmed:
   `e2e_agent_fix_loop.py`, `e2e_catalog.py`, `e2e_select.py`, and the
   `AGENT_BACKENDS_FILE` JSON. Keep `e2e_parallel_db_pool.py` only if you still
   want sharded parallelism (e2e-ai is sequential by design — that is the point:
   deterministic, one failure at a time, full history per test).

7. **Wire CI/Make targets** to the new commands:
   `e2e-ai discover`, `e2e-ai run` (fast triage), `e2e-ai repair` (unattended).

## Sequencing vs. sharding

fr-two's pool runs shards in parallel for speed. e2e-ai is deliberately
sequential: it fixes one failure at a time with the full context of prior
attempts, which is what makes the "keep going until green" loop tractable and
its history useful. Use fr-two's sharded pool for fast full-suite CI signal, and
e2e-ai for the unattended repair loop. They can share the same Postgres service.
