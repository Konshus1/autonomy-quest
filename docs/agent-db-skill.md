# The agent DB skill (`aqdb`) — #4834

The seeded act-phase agent (codex now, claude-code later) gets a **reliable, curated** way to
read and write the business database, delivered as a **skill-with-a-script** rather than an MCP
server (operator preference: scripts + skills are more reliable than MCP).

## What ships

- `agent_skills/db/aqdb` — executable Python CLI (psycopg2). Reads the restricted `aq_actor`
  DSN from `AQ_DB_URL` at runtime (never hardcoded). Curated, parameterized command surface:
  `measure`, `add-customer`, `add-subscription`, `list-customers`, `query`, `--help`.
- `agent_skills/db/SKILL.md` — the skill doc: the command surface + when/how to use it
  (do genuine research first, then record real results; never fabricate).
- `agent_skills/AGENTS.md` — the standing-instructions template seeded into the agent workspace.

## How it reaches the agent (injection)

- **Image**: `container/Dockerfile` does `COPY agent_skills/ /app/agent_skills/`, makes `aqdb`
  executable, and symlinks it onto PATH: `/usr/local/bin/aqdb -> /app/agent_skills/db/aqdb`.
- **Workspace**: `container/app-entrypoint.sh` runs `seed_agent_workspace` on startup — if
  `$AQ_ACT_WORKSPACE/AGENTS.md` (default `/workspace`, a durable volume) does not exist, it
  copies the template there so codex, running in `/workspace`, discovers it. Seeding is
  **idempotent** (never overwrites an existing AGENTS.md) and **fail-safe** (guarded with
  `seed_agent_workspace || true`; a seeding failure can never break the entrypoint or loop).

## Safety / invariants

- **Curated writes only.** Writes go exclusively through `add-customer` / `add-subscription`
  with every value bound as a SQL parameter. There is no raw-write path.
- **`query` is read-only, fail-closed.** It rejects anything that is not a single `SELECT`/
  `WITH` (no writes, DDL, comments, or `;`-chaining) and runs inside a DB-enforced read-only
  session as defense-in-depth.
- **Least privilege.** The DSN is the `aq_actor` role: SELECT everywhere; INSERT/UPDATE/DELETE
  only on `customers` and `subscriptions`; no reach to authority/evidence tables (runs, work,
  learnings, causal_*). The role bounds the blast radius even if the script had a bug.
- **No new capability.** The skill exposes only what `aq_actor` already has.

## Production default unchanged

A stack with no business mission behaves identically: seeding an AGENTS.md into an otherwise
empty workspace and adding a script to PATH does not alter the loop's decision path. The
seeding is skipped when an AGENTS.md is already present.

## Tests

- `tests/test_agent_db_skill.py` — `measure` returns the count; `add-customer`/`add-subscription`
  insert and are parameterized; `add-subscription` validates the customer exists; the `query`
  guard accepts reads and rejects INSERT/UPDATE/DELETE/DDL/comment/`;`-chaining/writable-CTE;
  missing/blank `AQ_DB_URL` fails clearly.
- `tests/test_agent_db_injection.py` — Dockerfile COPY + PATH symlink contract; entrypoint
  seeding runs the real shell function to prove it seeds an empty workspace, is idempotent, and
  is fail-safe when the workspace or template is missing.
