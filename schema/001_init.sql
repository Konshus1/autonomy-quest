-- autonomy-quest — the schema the loop lives in.
--
-- Four tables and a graph. The shape of this schema IS the thesis:
--   work        — what it decided to do
--   runs        — what happened when it did it            (act + record)
--   learnings   — what it concluded from that             (learn)
--   measurements— where the mission's number actually is  (ground truth)
--
-- verify.sh gates on runs JOIN learnings. That join is deliberate: a run without a learning
-- is automation. The learning is what makes it evolution. Do not make learnings nullable-by-
-- convenience or the acceptance gate becomes satisfiable by a system that never learned.

BEGIN;

-- AGE IS OPTIONAL — see 000_extensions.sql for the full reasoning and evidence (BB #2617).
-- `LOAD` is the reason this needs a guard of its own: unlike CREATE EXTENSION IF NOT EXISTS, it
-- has no tolerant form and errors outright when the library is absent, taking the whole
-- transaction (and therefore every table below) with it.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'age') THEN
    CREATE EXTENSION IF NOT EXISTS age;
    LOAD 'age';
  END IF;
END
$$;

-- SEARCH PATH ORDER IS LOAD-BEARING. public FIRST.
--
-- This said `SET search_path = ag_catalog, "$user", public` — ag_catalog first, copied from AGE's
-- own quickstart. Which means every CREATE TABLE below landed INSIDE ag_catalog rather than
-- public. The tables existed; they were just in AGE's schema. Then 002_blackboard.sql opened a
-- fresh session with a normal search_path and died with:
--
--     ERROR: relation "runs" does not exist
--
-- ...about a table that had just been created successfully. Only reachable with the graph
-- ENABLED — the graph:none path strips these lines — so no instance hit it until the first box
-- that actually turned AGE on.
--
-- AGE's functions are schema-qualified where they are used (ag_catalog.create_graph,
-- ag_catalog.cypher), so ag_catalog does not need to come first. OUR tables do.
SET search_path = public, ag_catalog, "$user";

-- ---------------------------------------------------------------------------
-- WORK — what the loop decided to do, and why.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS work (
  id            bigserial PRIMARY KEY,
  created_at    timestamptz NOT NULL DEFAULT now(),
  kind          text        NOT NULL,          -- from the template: outreach, delivery, ...
  summary       text        NOT NULL,
  rationale     text        NOT NULL,          -- why THIS moves the mission's number
  -- Autonomy gate. Work above the instance's level parks here instead of executing.
  requires_human boolean    NOT NULL DEFAULT false,
  approved_at   timestamptz,
  status        text        NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','awaiting_human','running','done','abandoned'))
);

-- ---------------------------------------------------------------------------
-- RUNS — one execution of one piece of work. This is the "act + record" half.
--
-- outcome IS NULL means it never finished. A row with completed_at set but outcome
-- NULL is a bug, not a success — the CHECK below makes that state unrepresentable
-- rather than merely discouraged.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS runs (
  id            bigserial PRIMARY KEY,
  work_id       bigint      NOT NULL REFERENCES work(id) ON DELETE CASCADE,
  started_at    timestamptz NOT NULL DEFAULT now(),
  completed_at  timestamptz,
  outcome       text,                          -- what actually happened. Plain language.
  succeeded     boolean,
  cost_usd      numeric(10,4) NOT NULL DEFAULT 0,
  tokens_in     bigint      NOT NULL DEFAULT 0,
  tokens_out    bigint      NOT NULL DEFAULT 0,
  error         text,
  rolled_back   boolean     NOT NULL DEFAULT false,   -- feeds the autonomy ratchet
  CONSTRAINT completed_runs_have_an_outcome
    CHECK (completed_at IS NULL OR outcome IS NOT NULL)
);

-- ---------------------------------------------------------------------------
-- LEARNINGS — the "learn" half, and the reason this system is not a cron job.
--
-- Every learning points back at the run that produced it. Learnings with no run are
-- opinions; we don't store opinions.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS learnings (
  id            bigserial PRIMARY KEY,
  run_id        bigint      NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  created_at    timestamptz NOT NULL DEFAULT now(),
  insight       text        NOT NULL,          -- what it now believes that it didn't before
  evidence      text        NOT NULL,          -- what in the run supports that
  -- Does this only apply here, or would it apply to any instance? The generalisable ones
  -- are the ones worth sharing (opt-in, off by default) — see docs/what-this-is.md.
  scope         text        NOT NULL DEFAULT 'local'
                CHECK (scope IN ('local','generalisable')),
  confidence    real        NOT NULL DEFAULT 0.5 CHECK (confidence BETWEEN 0 AND 1),
  superseded_by bigint      REFERENCES learnings(id)   -- beliefs get revised, not deleted
);

-- ---------------------------------------------------------------------------
-- MEASUREMENTS — the mission's number, over time. Ground truth, sampled.
--
-- The system reads THIS to know whether it is winning. It does not read its own
-- opinion of how it's doing. A loop that grades its own homework is not aimed at
-- anything real.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS measurements (
  id            bigserial PRIMARY KEY,
  taken_at      timestamptz NOT NULL DEFAULT now(),
  metric        text        NOT NULL,          -- mission.measure.what
  value         numeric     NOT NULL,
  source        text        NOT NULL           -- mission.measure.where — HOW we got it
);

CREATE INDEX IF NOT EXISTS runs_completed_idx     ON runs (completed_at DESC) WHERE completed_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS learnings_run_idx      ON learnings (run_id);
CREATE INDEX IF NOT EXISTS learnings_scope_idx    ON learnings (scope) WHERE superseded_by IS NULL;
CREATE INDEX IF NOT EXISTS measurements_metric_idx ON measurements (metric, taken_at DESC);

-- ---------------------------------------------------------------------------
-- THE GRAPH — relationships, in the same database. This is what AGE buys us.
--
-- Rows tell you WHAT happened. The graph tells you what RESEMBLES what: this failure
-- is the same shape as that one; this decision caused that outcome; these two pieces of
-- work rhyme. That is how the system reasons across its history instead of merely
-- accumulating it — and it needs no second datastore, which is what keeps the whole
-- system in one container.
-- ---------------------------------------------------------------------------
-- NOTE: the name must be at least THREE characters.
--
-- Apache AGE rejects 1-2 character graph names with 'ERROR: graph name is invalid' — a constraint
-- that appears NOWHERE in its documentation (there is an open issue on the AGE dev list about
-- exactly this). We used 'aq'. It failed on the first box that actually enabled the graph, and the
-- error message says nothing about length.
-- ...and create_graph is NOT idempotent: a second run raises 'graph already exists'. Every other
-- statement in this file is IF NOT EXISTS, and install.sh/sync.sh both re-apply it by design, so
-- the graph must guard itself too.
-- Guarded on the SCHEMA, not the extension list: `ag_catalog.ag_graph` is only resolvable once AGE
-- is actually installed, and PL/pgSQL parses the body of the inner block at execution time. A
-- reference to a missing schema inside a branch that never runs is still fine, but the lookup
-- itself must not be attempted — hence the to_regclass test rather than a direct query.
DO $$
BEGIN
  IF to_regclass('ag_catalog.ag_graph') IS NOT NULL THEN
    IF NOT EXISTS (SELECT 1 FROM ag_catalog.ag_graph WHERE name = 'autonomy_quest') THEN
      PERFORM ag_catalog.create_graph('autonomy_quest');
    END IF;
  END IF;
END
$$;

-- Node labels:  (:Work) (:Run) (:Learning) (:Pattern)
-- Edge labels:  (:Run)-[:EXECUTED]->(:Work)
--               (:Learning)-[:DERIVED_FROM]->(:Run)
--               (:Learning)-[:RESEMBLES]->(:Learning)
--               (:Pattern)-[:GENERALISES]->(:Learning)
--
-- Example — what has it learned that resembles what it's about to do?
--   SELECT * FROM cypher('autonomy_quest', $$
--     MATCH (l:Learning)-[:RESEMBLES]->(:Learning)-[:DERIVED_FROM]->(r:Run)
--     WHERE r.kind = 'outreach' RETURN l.insight
--   $$) AS (insight agtype);

COMMIT;
