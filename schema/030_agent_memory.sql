-- Agent informal memory substrate (#4834): a SEPARATE schema for the seeded agent's own
-- accumulated knowledge, distinct from both the business data (customers/subscriptions) and the
-- governed authority/evidence tables. Two tiers:
--
--   * SEMANTIC (pgvector): agent_memory.notes(embedding vector(384)) + cosine index, written by
--     `aqmem remember`, read by `aqmem recall` (recall-by-meaning).
--   * WORLD MODEL (Apache AGE): graph `world_model`, written by `aqmem relate`, read by
--     `aqmem world`.
--
-- Memory lives in the DB so a REPLICA inherits the parent's accumulated memory. The tool runs as
-- the restricted aq_actor role, so this migration GRANTs aq_actor write on ONLY these new memory
-- objects. It does NOT touch the authority-table REVOKEs in schema/999_container_role_grants.sql:
-- a raw INSERT into learnings/runs/work/causal_* stays denied. Least privilege preserved.
--
-- Idempotent + existence-guarded: applied by scripts/apply_migrations.py on every startup of
-- every app container (multiple, possibly concurrent), so every statement must be safe to re-run.
-- pgvector is a hard requirement (schema/000_extensions.sql); Apache AGE is OPTIONAL there, so the
-- graph half is guarded and MUST NOT fail the migration on a server without AGE.

BEGIN;

-- pgvector is guaranteed present (000_extensions.sql fails loudly otherwise), but keep the guard
-- so this file is self-contained and re-runnable.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE SCHEMA IF NOT EXISTS agent_memory;

CREATE TABLE IF NOT EXISTS agent_memory.notes (
  id          bigserial PRIMARY KEY,
  instance_id text        NOT NULL DEFAULT 'local',
  kind        text        NOT NULL DEFAULT 'learning',
  text        text        NOT NULL,
  embedding   vector(384) NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  metadata    jsonb       NOT NULL DEFAULT '{}'::jsonb
);

-- HNSW cosine index: recall orders by `embedding <=> query`. HNSW needs no training pass and works
-- on an empty/growing table (unlike ivfflat), which matches a memory store that starts empty and
-- accumulates across generations. Requires pgvector >= 0.5 (the container image ships current).
CREATE INDEX IF NOT EXISTS agent_memory_notes_embedding_cos_idx
  ON agent_memory.notes USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS agent_memory_notes_kind_idx ON agent_memory.notes (kind);
CREATE INDEX IF NOT EXISTS agent_memory_notes_instance_idx ON agent_memory.notes (instance_id);

COMMIT;

-- ---------------------------------------------------------------------------
-- Least-privilege grants for the semantic tier (container roles only).
-- External installs without the aq_actor role skip this block.
-- ---------------------------------------------------------------------------
DO $mem_grants$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aq_actor') THEN
    RAISE NOTICE 'aq_actor role absent; skipping agent_memory grants';
    RETURN;
  END IF;
  -- aqmem needs to reach into the schema and append + read notes. It does NOT get UPDATE/DELETE
  -- (memory is append-only) and it does NOT get CREATE on the schema (it cannot add tables). This
  -- is strictly narrower than the authority tables it remains revoked from.
  EXECUTE 'GRANT USAGE ON SCHEMA agent_memory TO aq_actor';
  EXECUTE 'GRANT SELECT, INSERT ON agent_memory.notes TO aq_actor';
  EXECUTE 'GRANT USAGE, SELECT ON SEQUENCE agent_memory.notes_id_seq TO aq_actor';
END
$mem_grants$;

-- ---------------------------------------------------------------------------
-- World-model graph (Apache AGE). OPTIONAL: guarded so a server without AGE still applies cleanly
-- (mirrors schema/000_extensions.sql). create_graph is not IF-NOT-EXISTS, so guard on ag_graph.
-- ---------------------------------------------------------------------------
DO $age_world$
DECLARE
  has_actor boolean := EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aq_actor');
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'age') THEN
    RAISE NOTICE 'AQ: Apache AGE not available — skipping world_model graph init. aqmem''s '
                 'semantic tier still works; the graph tier is inert until AGE is present.';
    RETURN;
  END IF;

  CREATE EXTENSION IF NOT EXISTS age;
  LOAD 'age';
  PERFORM set_config('search_path', 'public, ag_catalog, "$user"', true);

  IF NOT EXISTS (SELECT 1 FROM ag_catalog.ag_graph WHERE name = 'world_model') THEN
    PERFORM ag_catalog.create_graph('world_model');
    RAISE NOTICE 'AQ: created AGE graph world_model';
  END IF;

  IF has_actor THEN
    -- Let aq_actor run Cypher against world_model. AGE creates label tables lazily (a new edge
    -- label -> a new table in the world_model schema), so aq_actor needs CREATE on that schema and
    -- default privileges on its future tables/sequences. This is scoped to the memory graph schema
    -- only; it grants nothing on public authority/evidence tables.
    EXECUTE 'GRANT USAGE, CREATE ON SCHEMA world_model TO aq_actor';
    EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA world_model TO aq_actor';
    EXECUTE 'GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA world_model TO aq_actor';
    EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA world_model '
            'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO aq_actor';
    EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA world_model '
            'GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO aq_actor';
    -- ag_catalog holds cypher()/agtype and the label-id bookkeeping the query touches.
    EXECUTE 'GRANT USAGE ON SCHEMA ag_catalog TO aq_actor';
    EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA ag_catalog TO aq_actor';
    EXECUTE 'GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA ag_catalog TO aq_actor';
  END IF;
EXCEPTION
  WHEN insufficient_privilege THEN
    -- e.g. LOAD 'age' refused on a server that does not preload it. Non-fatal: the migration owner
    -- keeps the relational schema; the graph tier initializes on a server that offers AGE.
    RAISE NOTICE 'AQ: world_model graph init skipped (insufficient privilege for AGE): %',
                 SQLERRM;
  WHEN OTHERS THEN
    RAISE NOTICE 'AQ: world_model graph init skipped (non-fatal): %', SQLERRM;
END
$age_world$;
