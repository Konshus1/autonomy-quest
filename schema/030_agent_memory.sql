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
  obj record;
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
    -- aqmem runs Cypher against world_model as the restricted aq_actor role. AGE demands that the
    -- role mutating a graph OWN the graph's label tables: DML grants alone are NOT enough — a
    -- vertex/edge label create fails with "must be owner of table _ag_label_vertex". So hand
    -- ownership of the world_model graph — AND ONLY that graph — to aq_actor. New label tables that
    -- aqmem creates lazily are then owned by aq_actor automatically (no default-privileges needed).
    -- This is scoped to the memory graph; it grants NOTHING on any other graph, on public
    -- authority/evidence tables, or on ag_catalog's global, shared rows.
    EXECUTE 'ALTER SCHEMA world_model OWNER TO aq_actor';
    FOR obj IN
      SELECT c.relkind, c.relname
      FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
      WHERE n.nspname = 'world_model' AND c.relkind IN ('r', 'p', 'S')
    LOOP
      BEGIN
        IF obj.relkind = 'S' THEN
          -- Sequences linked to a table column follow that table's owner and cannot be reassigned
          -- on their own; skip those (the ALTER TABLE above already moved them) and reassign only
          -- standalone sequences such as world_model._label_id_seq.
          EXECUTE format('ALTER SEQUENCE world_model.%I OWNER TO aq_actor', obj.relname);
        ELSE
          EXECUTE format('ALTER TABLE world_model.%I OWNER TO aq_actor', obj.relname);
        END IF;
      EXCEPTION WHEN dependent_objects_still_exist OR feature_not_supported THEN
        NULL;  -- linked sequence: ownership already follows its table
      END;
    END LOOP;

    -- Minimal access to the GLOBAL AGE catalog (shared by EVERY graph). Cypher planning READS
    -- ag_graph/ag_label, and registering a NEW label INSERTs one ag_label row (its id comes from
    -- world_model._label_id_seq, which aq_actor now owns). Deliberately NO update/delete anywhere in
    -- ag_catalog and NO write on ag_graph.
    --
    -- BOUNDED, NOT AIRTIGHT (no overclaiming): aq_actor is graph-scoped for the SERIOUS operations —
    -- it cannot READ, MODIFY, or DESTROY another graph's data (USAGE on that graph's schema is
    -- denied), and it cannot UPDATE/DELETE ag_graph/ag_label. HOWEVER, because AGE auto-registers a
    -- label during a MERGE run as aq_actor, aq_actor RETAINS INSERT on the shared ag_catalog.ag_label
    -- and CAN therefore insert phantom label rows into ANOTHER graph's catalog. The residual is
    -- bounded to catalog pollution / name-squatting / DoS — NOT data compromise (it cannot read or
    -- write another graph's actual vertices/edges). See the follow-up TODO below.
    -- TODO(#4834 follow-up, aqmem-catalog-hardening): row-scope ag_label INSERT to graph=world_model
    -- via a SECURITY DEFINER register-label function (owned by aq_owner, EXECUTE to aq_actor) OR RLS
    -- on ag_label; then drop the direct INSERT grant. Deferred per operator (bounded DoS-class
    -- residual, no data compromise).
    EXECUTE 'GRANT USAGE ON SCHEMA ag_catalog TO aq_actor';
    EXECUTE 'GRANT SELECT ON ALL TABLES IN SCHEMA ag_catalog TO aq_actor';
    EXECUTE 'GRANT INSERT ON ag_catalog.ag_label TO aq_actor';
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
