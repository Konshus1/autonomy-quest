# The agent memory skill (`aqmem`) — #4834

The companion to `aqdb`. Where `aqdb` is the agent's bridge to the **governed** business data,
`aqmem` is the agent's own **informal** memory, so a seeded agent gets **smarter over time** and a
**replica inherits the parent's accumulated memory** (memory lives in the database, so it travels
with a replicated stack — compounding intelligence across generations).

## Two tiers

- **Semantic (pgvector)** — `remember` a free-text note (embedded locally, 384-dim); `recall`
  returns the notes nearest in *meaning* to a described situation (cosine distance, nearest first).
  Recall-by-meaning, not keyword.
- **World model (Apache AGE)** — `relate "<A>" "<EDGE>" "<B>"` records a typed relationship in the
  graph `world_model`; `world "<entity>"` returns that entity's neighborhood.

## Local, baked embeddings (operator decision)

Embeddings come from a **local model baked into the image** — no API key, no runtime network,
self-contained, survives replication. `fastembed` (ONNX Runtime, no torch) with
`BAAI/bge-small-en-v1.5` (384-dim, cosine). The model is **downloaded at docker-build time** into
`FASTEMBED_CACHE_PATH=/opt/aq/models` and the build **asserts** the cache is populated and the
dimension is 384, so a network hiccup fails the BUILD rather than surfacing at runtime.

## What ships

- `agent_skills/memory/aqmem` — executable Python CLI (psycopg2). Reads the same restricted
  `aq_actor` DSN from `AQ_DB_URL` (never hardcoded). Commands: `remember`, `recall`, `relate`,
  `world`, `--help`.
- `agent_skills/memory/SKILL.md` — the skill doc (when/how to use it; never fabricate).
- `agent_skills/AGENTS.md` — **extended** (not forked) with an `aqmem` section alongside `aqdb`.
- `schema/030_agent_memory.sql` — the migration (below).

## How it reaches the agent (injection)

- **Image**: `container/Dockerfile` already `COPY`s `agent_skills/` wholesale; the build makes
  `aqmem` executable, bakes the local model, and symlinks it onto PATH
  (`/usr/local/bin/aqmem -> /app/agent_skills/memory/aqmem`).
- **Workspace**: `container/app-entrypoint.sh::seed_agent_workspace` seeds the shared AGENTS.md
  template (now covering both skills) — unchanged, still **idempotent** and **fail-safe**.

## Migration (`schema/030_agent_memory.sql`)

- Creates schema `agent_memory` + `agent_memory.notes(embedding vector(384))` with an HNSW cosine
  index; initializes the AGE graph `world_model`.
- GRANTs `aq_actor` `USAGE`/`SELECT`/`INSERT` on the memory schema (append-only; no UPDATE/DELETE,
  no `CREATE`) — and **nothing** on the authority/evidence tables. The
  `999_container_role_grants.sql` default-deny (REVOKE SELECT ON ALL + REVOKE writes on
  `runs`/`work`/`learnings`/`causal_*`) stays intact: as `aq_actor`, even a raw **SELECT** of
  `learnings` → permission denied, and INSERT likewise.
- For the `world_model` graph, `aq_actor` is made the **owner** of that graph's label
  tables/sequences (scoped to the `world_model` schema only). AGE requires ownership — not just DML
  — for a non-superuser to create labels; DML-only grants fail with *"must be owner of table
  _ag_label_vertex"*. On the shared `ag_catalog` it gets only `USAGE` + `SELECT` + `INSERT` on
  `ag_label` (to register a new label) — deliberately **no** write on `ag_graph` and **no**
  update/delete anywhere, so it can never drop or rename **another** graph's catalog rows (e.g. the
  loop's `autonomy_quest` graph).
- **Idempotent + existence-guarded**: safe to re-run (`apply_migrations.py` runs it on every
  startup of every app container). The AGE half is optional and guarded — a server without AGE
  applies the migration cleanly (the semantic tier still works; the graph tier is inert).
- **AGE must be preloaded.** `relate`/`world` run Cypher as the non-superuser `aq_actor`, which
  cannot `LOAD 'age'`; AGE's `cypher()` is unusable in a session where the library was never loaded.
  The DB service therefore sets `shared_preload_libraries=age` (see `docker-compose.yml`). Without
  it the world-model tier fails **clean** (`aqmem: world-model tier unavailable …`, exit 2); the
  semantic tier is unaffected. (This same gap is why the loop's own `autonomy_quest` graph mirror
  silently never wrote on pre-preload stacks.)

## Safety / invariants

- **Curated ops only; everything parameterized.** Note text, entity names, and property values all
  cross into SQL/Cypher as bound parameters. The one thing that cannot be a bound parameter — a
  Cypher relationship **label** — is allowlist-validated (`^[A-Za-z_][A-Za-z0-9_]*$`, spaces/hyphens
  mapped to underscore) and **fail-closed** (exit 2, no DB mutation) on anything else.
- **Least privilege.** Same `aq_actor` role as `aqdb`, plus a write grant on the memory schema and
  graph only.
- **No note text leaves the machine** — embeddings are local.

## Production default unchanged

A stack with no business mission behaves identically: a new script on PATH and a new schema the
loop never consults do not alter the loop's decision path.

## Tests

- `tests/test_agent_mem_skill.py` — a fake DB models pgvector ordering and the AGE graph:
  `remember`/`recall` **round-trip on CONTENT** (the semantically-similar query returns the RIGHT
  note first; an unrelated query does NOT); values are bound not inlined; the query vector is
  parameterized; embedding dimension is enforced; a missing model cache fails clearly; label
  sanitization accepts identifiers and **rejects injection fail-closed** with no mutation; a
  `relate`d edge shows up in the neighborhood and an unrelated entity does not.
- `tests/test_agent_mem_injection.py` — Dockerfile PATH + model-bake contract (build-time
  assertions); AGENTS.md covers both skills; migration is idempotent/guarded and grants `aq_actor`
  only on memory; entrypoint seeding still fail-safe.
- `tests/test_agent_mem_pg.py` — **live-DB proof harness** (skipped without DSNs): as `aq_actor`,
  writes to `agent_memory` succeed but a raw INSERT into `learnings` fails with permission denied;
  the migration is idempotent; the `world_model` graph is writable by `aq_actor`.
