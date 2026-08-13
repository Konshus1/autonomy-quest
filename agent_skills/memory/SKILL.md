---
name: aqmem
description: >-
  Your own two-tier informal memory so you get smarter over time. A semantic note store
  (recall-by-meaning via pgvector) and a world model (an Apache AGE graph of entities +
  relationships). Use it to `recall` relevant past notes BEFORE acting and to `remember` real
  learnings AFTER, and to build a world model with `relate`/`world`. Memory lives in the DB, so a
  replica inherits it. Never fabricate — memory is only useful if it is honest.
---

# aqmem — two-tier agent memory

`aqmem` is an executable on your PATH. It is separate from `aqdb` (the governed business data):
`aqmem` is *your own* accumulated, informal knowledge. It reads the same restricted `AQ_DB_URL`
credential you already have; you never pass credentials yourself. Embeddings are computed by a
**local model baked into the image** — no API key, no network.

Run `aqmem --help` for the exact command surface.

## Two tiers

**Semantic notes (recall by meaning).** A note is free text plus a locally-computed 384-dim
embedding. `recall` returns the notes nearest in *meaning* to a situation you describe — not a
keyword match.

**World model (a graph).** Entities and typed relationships in the Apache AGE graph `world_model`.
`relate` records `(A)-[:EDGE]->(B)`; `world` returns an entity's neighborhood.

Both live in the database, so a **replica inherits the parent's accumulated memory** — you are
adding to the intelligence of every generation that follows.

## Commands

| Command | What it does |
| --- | --- |
| `aqmem remember "<text>" [--kind learning] [--metadata '<json>']` | Embed the note locally and store it. Recallable by meaning. |
| `aqmem recall "<situation>" [--k 5] [--kind <k>]` | Return the k notes nearest in meaning (cosine distance, nearest first). |
| `aqmem relate "<A>" "<edge>" "<B>" [--props '<json>']` | Record a typed relationship in the world model. Idempotent (MERGE). |
| `aqmem world "<entity>" [--depth 1]` | Show an entity's neighborhood (connected entities + edges). |

Add `--json` to any command for machine-readable output.

## How to use it

1. **Before acting, recall.** `aqmem recall "how do I price an SMB analytics prospect"` surfaces
   what past-you already learned. Read it before you re-derive it.
2. **After learning something real, remember it.** Once you have done genuine work and learned
   something that would help next time, store it:
   ```
   aqmem remember "SMB analytics buyers churn if onboarding takes >1 week; lead with a 3-day setup" --kind learning
   ```
3. **Maintain a world model.** Capture the entities and relationships you discover:
   ```
   aqmem relate "Acme Co" "OPERATES_IN" "logistics"
   aqmem relate "Acme Co" "COMPETES_WITH" "Globex"
   aqmem world "Acme Co"
   ```

## Safety (what this tool will and will not do)

- **Least privilege.** `aqmem` writes only the `agent_memory` schema and the `world_model` graph.
  It cannot touch the business tables or the authority/evidence tables — the same restricted role
  as `aqdb`, with an added write grant on memory only.
- **Everything is parameterized.** Note text, entity names, and property values all cross into SQL
  / Cypher as bound parameters. The one thing that cannot be a bound parameter — a relationship
  label — is allowlist-validated (`^[A-Za-z_][A-Za-z0-9_]*$`); anything else is refused
  fail-closed, with no database mutation.
- **Local embeddings.** The embedding model is baked into the image. No embedding API is called;
  no note text leaves the machine.

## Do not fabricate

Memory compounds — a false note misleads every future generation that inherits it. `remember`
only things you actually learned; `relate` only relationships you actually found. An honest,
smaller memory beats a large invented one.
