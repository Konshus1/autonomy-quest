-- 009_causal_edges.sql — fresh-instance causal substrate, additive.
--
-- Implements T5 (#4897): the causal-edge AGE schema substrate.
-- Source of truth: BB decision #746 (causal edge model) + #829 (JEPA enrichment).
--
-- THIS MIGRATION IS ADDITIVE ONLY:
--   * It creates five durable tables: causal_edge, planning_prediction, ralph_causal_edges,
--     ralph_frame_dimensions, and the intentionally empty causal_principle corpus.
--   * It does NOT alter, drop, or rename any table from 001–008.
--   * It does NOT touch work / runs / learnings / measurements / shared_learnings.
--   * It is loaded for every fresh instance from schema/ after migrations 001-008.
--     The experimental source remains under experiments/causal_edges/ for provenance.
--
-- A causal claim is ONE EDGE in the graph (AGE), decomposed into TWO LINKED CLAIMS:
--   action -> direct effect   : mechanical, can become a DETERMINISTIC SCRIPT (in-scope guarantee)
--   direct effect -> mission   : GOAL-POTENCY, stays PROBABILISTIC (the world is in the loop)
--
-- The edge carries THREE INDEPENDENT WEIGHTS (three queryable dials):
--   formality_weight   — epistemic confidence in the causality (grows from interventional evidence)
--   strictness_weight  — how hard it binds behavior: advisory / soft-constraint / hard-guardrail
--   directness_weight  — judgment -> structured predicate -> executable script (action determinism)
--
-- Executor slot: primarily A (actuator). B (verifier) / C (constraint) only when warranted.
--
-- Surprise-driven learning: each edge carries a predicted_certainty; actual vs predicted = surprise.
-- (Surprise resolution + gated weight update is a LATER slice — this migration only stores the
--  prediction bookkeeping. planning_check.py is RECORD-ONLY.)
--
-- JEPA enrichment (BB #829): the planning prediction records a FULL outcome representation,
-- not just a certainty scalar: which principles will be supported / challenged, which edges
-- will be stressed. One prediction row per plan.

BEGIN;

SET search_path = public, ag_catalog, "$user";

-- ---------------------------------------------------------------------------
-- causal_edge — one causal claim, two linked claims, three weights, executor slot.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS causal_edge (
    edge_id             bigserial PRIMARY KEY,

    -- the two linked claims
    source_action       text        NOT NULL,          -- action -> direct effect (mechanical, scriptable)
    direct_effect       text        NOT NULL,          -- the guaranteed-in-scope direct outcome
    mission_measure     text        NOT NULL,          -- direct effect -> mission measure (probabilistic goal-potency)

    -- three independent weights (each a queryable dial, 0..1)
    formality_weight    real        NOT NULL DEFAULT 0.5 CHECK (formality_weight  BETWEEN 0 AND 1),
    strictness_weight   real        NOT NULL DEFAULT 0.5 CHECK (strictness_weight BETWEEN 0 AND 1),
    directness_weight   real        NOT NULL DEFAULT 0.5 CHECK (directness_weight BETWEEN 0 AND 1),

    -- executor slot: A = actuator (default), B = verifier, C = constraint
    executor_slot       char(1)     NOT NULL DEFAULT 'A'
                        CHECK (executor_slot IN ('A','B','C')),

    -- surprise fields
    predicted_certainty real        CHECK (predicted_certainty BETWEEN 0 AND 1),

    -- bookkeeping per edge (BB #746)
    scope_conditions    text,                          -- under what conditions this edge holds
    evidence_run_ids    bigint[]    NOT NULL DEFAULT '{}',  -- runs that produced interventional evidence
    support_count       integer     NOT NULL DEFAULT 0,     -- supporting observations
    falsified_by        bigint,                         -- run that falsified this edge (NULL = not falsified)

    last_validated      timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

-- A falsified edge must point at the run that falsified it. Unrepresentable otherwise.
ALTER TABLE causal_edge DROP CONSTRAINT IF EXISTS falsified_edge_has_evidence;
ALTER TABLE causal_edge ADD CONSTRAINT falsified_edge_has_evidence CHECK (
    falsified_by IS NULL OR falsified_by > 0
);

CREATE INDEX IF NOT EXISTS causal_edge_executor_idx  ON causal_edge (executor_slot);
CREATE INDEX IF NOT EXISTS causal_edge_falsified_idx ON causal_edge (falsified_by) WHERE falsified_by IS NOT NULL;
CREATE INDEX IF NOT EXISTS causal_edge_strict_idx    ON causal_edge (strictness_weight DESC);

-- ---------------------------------------------------------------------------
-- planning_prediction — RECORD-ONLY. One prediction per plan.
--
-- JEPA enrichment (#829): full outcome representation, not just a certainty scalar.
-- actual_outcome + surprise_level are filled LATER (on outcome); the planning check
-- only WRITES the predicted_* columns. Nothing here gates or redirects a plan.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS planning_prediction (
    prediction_id                       bigserial PRIMARY KEY,
    edge_id                             bigint      NOT NULL REFERENCES causal_edge(edge_id) ON DELETE CASCADE,
    plan_id                             text        NOT NULL,          -- opaque plan identifier (not a FK: plans are not rows yet)

    -- full outcome representation (BB #829 REC 2)
    predicted_principles_supported      jsonb       NOT NULL DEFAULT '[]'::jsonb,
    predicted_principles_challenged     jsonb       NOT NULL DEFAULT '[]'::jsonb,
    predicted_edges_stressed            jsonb       NOT NULL DEFAULT '[]'::jsonb,

    predicted_certainty                 real        CHECK (predicted_certainty BETWEEN 0 AND 1),

    -- filled later, on outcome. NULL until then = prediction not yet resolved.
    actual_outcome                      text,
    surprise_level                      real        CHECK (surprise_level BETWEEN 0 AND 1),

    recorded_at                         timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS planning_prediction_plan_idx  ON planning_prediction (plan_id);
CREATE INDEX IF NOT EXISTS planning_prediction_edge_idx  ON planning_prediction (edge_id);
CREATE INDEX IF NOT EXISTS planning_prediction_unresolved_idx
    ON planning_prediction (plan_id) WHERE actual_outcome IS NULL;



-- Runtime stores previously created lazily by Python.  They belong in the fresh-instance
-- schema so a restart never decides whether the durable structure exists.
CREATE TABLE IF NOT EXISTS ralph_causal_edges (
    cause  text NOT NULL,
    effect text NOT NULL,
    scope  text NOT NULL,
    edge   jsonb NOT NULL,
    PRIMARY KEY (cause, effect, scope)
);

CREATE TABLE IF NOT EXISTS ralph_frame_dimensions (
    id         text NOT NULL,
    status     text NOT NULL DEFAULT 'active'
               CHECK (status IN ('active', 'proposed', 'retired')),
    dimension  jsonb NOT NULL,
    PRIMARY KEY (id, status)
);


-- Empty-by-design principle corpus.  A public instance learns its own principles; it never
-- inherits fleet-specific content.  The shape matches the existing repository contract.
CREATE TABLE IF NOT EXISTS causal_principle (
    principle_id text PRIMARY KEY,
    principle_text text NOT NULL,
    tags jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(tags) = 'array'),
    status text NOT NULL DEFAULT 'provisional'
        CHECK (status IN ('provisional', 'active', 'challenged', 'demoted')),
    source text NOT NULL CHECK (length(trim(source)) > 0),
    graph_namespace text NOT NULL DEFAULT 'ralph_causal_principles_v0',
    context jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(context) = 'object'),
    failure_modes_addressed jsonb NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(failure_modes_addressed) = 'array'),
    formalization_hint text,
    created_on timestamptz NOT NULL DEFAULT now(),
    created_by text NOT NULL DEFAULT 'causal_principle_repository_v0',
    updated_on timestamptz NOT NULL DEFAULT now(),
    updated_by text NOT NULL DEFAULT 'causal_principle_repository_v0',
    is_active boolean NOT NULL DEFAULT true
);
CREATE INDEX IF NOT EXISTS idx_causal_principle_graph_namespace
    ON causal_principle (graph_namespace) WHERE is_active = true;

COMMIT;
